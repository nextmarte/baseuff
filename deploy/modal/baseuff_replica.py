"""Réplica de contingência do BaseUFF na Modal (armável sob demanda).

Sobe o MESMO caminho de serving da produção fora da UFF, para failover quando o
campus perde luz/internet:

- função ``mcp`` (CPU): Qdrant 1.18.2 restaurado do snapshot no Volume + servidor
  FastMCP montado igual ao ``scripts/serve.py``;
- classe ``Encoder`` (GPU T4, scale-to-zero): BGE-M3 + ColBERT + cross-encoder do
  ``packages/embed``, com os modelos baked na imagem (sobe sem internet no HF).
  O encoder NÃO tem URL pública — o server chama as funções GPU pela própria Modal
  (autenticado), então ninguém de fora consegue drenar créditos por ele.

Operação (ver docs/ARQUITETURA.md):

    scripts/replica.sh armar [--pin]   # modal deploy (+pin = 1 container quente)
    scripts/replica.sh desarmar        # modal app stop (gasto zero garantido)

Dados chegam pelo Volume ``baseuff-data`` via ``scripts/sync_replica.py`` (chamado
no fim do update.py diário). Desarmada por padrão: app parado não sobe nem cobra.
"""

from __future__ import annotations

import os
from pathlib import Path

import modal

# Na máquina local o arquivo vive em deploy/modal/ (o repo é a raiz 2 níveis acima);
# nos containers da Modal o módulo é montado em /root e o repo não existe — nem é
# preciso: os add_local_dir abaixo são resolvidos NO DEPLOY, a partir da máquina local.
_ARQUIVO = Path(__file__).resolve()
REPO = _ARQUIVO.parents[2] if len(_ARQUIVO.parents) > 2 else _ARQUIVO.parent
COLLECTION = "uff_chunks"
QDRANT_VERSION = "v1.18.2"  # mesma versão do ultron (compatibilidade de snapshot)
# --pin em replica.sh exporta MODAL_REPLICA_PIN=1 no deploy (1 container sempre quente)
PIN = int(os.environ.get("MODAL_REPLICA_PIN", "0"))

app = modal.App("baseuff-replica")
vol = modal.Volume.from_name("baseuff-data", create_if_missing=True)


# --- GPU: encoder + rerankers (mesmos modelos/código do skynet01) -----------------------


def _baixar_modelos() -> None:
    """Baixa os modelos no BUILD da imagem — em runtime não depende do HuggingFace."""
    from huggingface_hub import snapshot_download

    snapshot_download("BAAI/bge-m3")
    snapshot_download("BAAI/bge-reranker-v2-m3")


imagem_gpu = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install(
        "FlagEmbedding>=1.3",
        "sentence-transformers>=3.0",
        "sentencepiece",
        "protobuf",
    )
    .run_function(_baixar_modelos)
    .add_local_dir(str(REPO / "packages/embed/uff_embed"), "/root/uff_embed")
)


@app.cls(
    image=imagem_gpu,
    gpu="T4",
    timeout=600,
    scaledown_window=300,
    max_containers=3,
    min_containers=PIN,
    retries=2,
)
@modal.concurrent(max_inputs=8)
class Encoder:
    @modal.enter()
    def carregar(self) -> None:
        import sys
        import threading

        sys.path.insert(0, "/root")
        from uff_embed.embedder import Bge
        from uff_embed.reranker import Reranker

        self.bge = Bge()
        self.rr = Reranker()
        # FlagEmbedding não é thread-safe (mesma pegadinha do serve_encoder.py)
        self.lock = threading.Lock()

    @modal.method()
    def encode(self, text: str) -> dict:
        with self.lock:
            enc = self.bge.encode_query(text)
        return {
            "dense": enc.dense,
            "sparse_indices": enc.sparse_indices,
            "sparse_values": enc.sparse_values,
        }

    @modal.method()
    def rerank(self, query: str, passages: list[str]) -> list[float]:
        with self.lock:
            return self.rr.scores(query, passages)

    @modal.method()
    def colbert_rerank(self, query: str, passages: list[str]) -> list[float]:
        with self.lock:
            return self.bge.colbert_scores(query, passages)


# --- CPU: Qdrant + servidor MCP ---------------------------------------------------------

imagem_server = (
    # Imagem oficial do Qdrant (binário em /qdrant/qdrant) + Python do serving por cima.
    modal.Image.from_registry(f"qdrant/qdrant:{QDRANT_VERSION}", add_python="3.12")
    # Mesmas versões do serving em produção no ultron (uv.lock) — menos surpresa.
    .pip_install(
        "fastmcp==3.4.3",
        "qdrant-client==1.18.0",
        "starlette==1.3.1",
        "uvicorn==0.50.2",
        "pydantic==2.13.4",
        "pydantic-settings==2.14.2",
        "httpx==0.28.1",
    )
    .add_local_dir(str(REPO / "packages/core/uff_core"), "/root/uff_core")
    .add_local_dir(str(REPO / "packages/server/uff_server"), "/root/uff_server")
)


class _HostLocal:
    """Reescreve o header Host para 127.0.0.1 antes de delegar ao app MCP.

    O transporte streamable-http do MCP tem proteção anti DNS-rebinding que valida o
    Host e só aceita localhost — em produção o Apache repassa ``Host: 127.0.0.1:8088``,
    mas atrás do ``*.modal.run`` o Host público chega cru e o MCP devolve 421.
    """

    def __init__(self, app) -> None:
        self._app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            headers = [(k, v) for k, v in scope["headers"] if k.lower() != b"host"]
            headers.append((b"host", b"127.0.0.1"))
            scope = dict(scope, headers=headers)
            # Sem redirect /mcp/ -> /mcp: o Location seria montado com o Host
            # reescrito acima e mandaria o cliente para https://127.0.0.1/...
            if scope.get("path") == "/mcp/":
                scope["path"] = "/mcp"
                scope["raw_path"] = b"/mcp"
        await self._app(scope, receive, send)


class _EncoderInterno:
    """QueryEncoder do retriever chamando a função GPU pela Modal (sem URL pública)."""

    def __init__(self, gpu: Encoder) -> None:
        self._gpu = gpu

    def encode_query(self, text: str):
        from uff_server.retriever import QueryVector

        data = self._gpu.encode.remote(text)
        return QueryVector(
            dense=data["dense"],
            sparse_indices=data["sparse_indices"],
            sparse_values=data["sparse_values"],
        )


class _RerankerInterno:
    """Protocolo Reranker sobre um modal.method (cross-encoder ou ColBERT)."""

    def __init__(self, metodo) -> None:
        self._metodo = metodo

    def rerank(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        return self._metodo.remote(query, passages)


@app.function(
    image=imagem_server,
    volumes={"/dados": vol},
    cpu=2.0,
    memory=6144,
    timeout=600,
    scaledown_window=300,
    max_containers=1,  # sessões do MCP streamable-http vivem num container só
    min_containers=PIN,
)
@modal.concurrent(max_inputs=20)
@modal.asgi_app(label="baseuff-mcp")
def mcp():
    import shutil
    import subprocess
    import sys
    import threading
    import time

    import httpx

    sys.path.insert(0, "/root")

    snap = Path("/dados/snapshots") / f"{COLLECTION}.snapshot"
    if not snap.exists():
        raise RuntimeError("snapshot ausente no Volume — rode scripts/sync_replica.py no ultron")

    # Qdrant local ao container, restaurado do snapshot (storage efêmero; a fonte de
    # verdade é o Volume, reescrito a cada sync do ultron).
    subprocess.Popen(
        ["/qdrant/qdrant", "--snapshot", f"{snap}:{COLLECTION}", "--force-snapshot"],
        cwd="/qdrant",
    )
    for _ in range(300):
        try:
            if httpx.get("http://127.0.0.1:6333/readyz", timeout=1.0).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(1)
    else:
        raise RuntimeError("qdrant não ficou pronto após restaurar o snapshot")

    # SQLite não convive bem com o filesystem do Volume: catálogo vai p/ disco local.
    # O querylog da réplica é efêmero (some no scaledown) — aceitável p/ contingência.
    shutil.copy("/dados/catalog.db", "/tmp/catalog.db")

    from qdrant_client import QdrantClient
    from uff_core.catalog import Catalog
    from uff_core.querylog import QueryLog
    from uff_server.admin import admin_data, render_admin_html, render_logout_html, verify_basic
    from uff_server.app import build_docs, create_app, render_docs_html
    from uff_server.auth import BearerAuthMiddleware
    from uff_server.reranker import CascadeReranker

    gpu = Encoder()
    encoder = _EncoderInterno(gpu)
    reranker = CascadeReranker(_RerankerInterno(gpu.colbert_rerank), _RerankerInterno(gpu.rerank))
    client = QdrantClient(url="http://127.0.0.1:6333", timeout=30)
    catalog = Catalog("/tmp/catalog.db")
    querylog = QueryLog("/tmp/queries.db")
    mcp_app = create_app(
        client, COLLECTION, encoder, reranker=reranker, catalog=catalog, querylog=querylog
    )

    tokens = "/dados/config/mcp_tokens.txt"
    pass_file = Path("/dados/config/admin_pass.hash")
    admin_kwargs = {}
    if pass_file.exists():
        admin_hash = pass_file.read_text().strip()
        admin_kwargs = {
            "admin_html": render_admin_html(),
            "admin_logout_html": render_logout_html(),
            "admin_provider": lambda p: admin_data(
                querylog,
                client,
                COLLECTION,
                catalog,
                "http://127.0.0.1:6333",  # sem microserviço encoder aqui; healthcheck é n/a
                p,
                encoder=encoder,
                reranker=reranker,
                tokens_path=tokens,
            ),
            "admin_authorized": lambda auth: verify_basic(auth, "admin", admin_hash),
        }

    def _aquecer_gpu() -> None:
        try:
            gpu.encode.remote("aquecimento da réplica")
        except Exception:
            pass  # aquecimento é best-effort; a 1ª consulta real aquece se preciso

    threading.Thread(target=_aquecer_gpu, daemon=True).start()

    def _recarregar_volume() -> None:
        # tokens/hash novos do sync ficam visíveis sem reiniciar o container
        while True:
            time.sleep(60)
            try:
                vol.reload()
            except Exception:
                pass

    threading.Thread(target=_recarregar_volume, daemon=True).start()

    return _HostLocal(
        BearerAuthMiddleware(
            mcp_app.http_app(stateless_http=True),  # como na produção: restart invisível
            tokens,
            docs_provider=lambda: build_docs(client, COLLECTION, catalog),
            html_renderer=render_docs_html,
            **admin_kwargs,
        )
    )
