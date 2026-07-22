"""Entrypoint do servidor MCP (ultron).

Liga o FastMCP ao Qdrant local e ao encoder remoto no host GPU:

    uv run python scripts/serve.py                 # stdio (Claude Code/Desktop)
    uv run python scripts/serve.py --http 8000     # HTTP (clientes remotos)
"""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn
from qdrant_client import QdrantClient
from uff_core.catalog import Catalog
from uff_core.config import Settings, sqlite_path
from uff_core.querylog import QueryLog
from uff_server.admin import admin_data, render_admin_html, render_logout_html, verify_basic
from uff_server.app import build_docs, create_app, render_docs_html
from uff_server.auth import BearerAuthMiddleware
from uff_server.encoder import BalancedEncoder, RemoteEncoder
from uff_server.reranker import BalancedReranker, CascadeReranker, ColbertReranker, RemoteReranker


def main() -> None:
    ap = argparse.ArgumentParser(description="Servidor MCP BaseUFF")
    ap.add_argument("--http", type=int, default=None, help="porta HTTP (default: stdio)")
    ap.add_argument(
        "--host",
        default="127.0.0.1",
        help="bind do HTTP (default: 127.0.0.1; a exposição pública passa por proxy TLS)",
    )
    args = ap.parse_args()

    settings = Settings()
    client = QdrantClient(url=settings.qdrant_url, timeout=30)
    # UFF_ENCODER_URL aceita várias URLs separadas por vírgula (um processo de encoder
    # por GPU no skynet01): com 2+, balanceia round-robin com failover — rajadas de
    # consultas concorrentes deixam de empilhar na fila de uma GPU só.
    urls = [u.strip() for u in settings.encoder_url.split(",") if u.strip()]
    if len(urls) == 1:
        encoder = RemoteEncoder(urls[0])
        colbert, cross = ColbertReranker(urls[0]), RemoteReranker(urls[0])
    else:
        encoder = BalancedEncoder([RemoteEncoder(u) for u in urls])
        colbert = BalancedReranker([ColbertReranker(u) for u in urls])
        cross = BalancedReranker([RemoteReranker(u) for u in urls])
    # Cascata: ColBERT (rápido) pré-seleciona, cross-encoder finaliza o topo.
    # Qualidade do cross-encoder (MRR 1.0) a ~0,65s/consulta (vs ~3s só cross-encoder).
    reranker = CascadeReranker(colbert, cross)
    catalog = Catalog(sqlite_path(settings.catalog_dsn))
    querylog = QueryLog(str(Path(settings.data_dir) / "queries.db"))
    collection = settings.qdrant_collection
    mcp = create_app(
        client, collection, encoder, reranker=reranker, catalog=catalog, querylog=querylog
    )

    if args.http:
        # Painel de admin em /mcp/admin (HTTP Basic: usuário 'admin' + senha em hash).
        pass_file = Path(settings.data_dir) / "admin_pass.hash"
        admin_hash = pass_file.read_text().strip() if pass_file.exists() else None
        admin_kwargs = {}
        if admin_hash:
            admin_kwargs = {
                "admin_html": render_admin_html(),
                "admin_logout_html": render_logout_html(),
                "admin_provider": lambda p: admin_data(
                    querylog,
                    client,
                    collection,
                    catalog,
                    settings.encoder_url,
                    p,
                    encoder=encoder,
                    reranker=reranker,
                    tokens_path=settings.mcp_tokens_path,
                ),
                "admin_authorized": lambda auth: verify_basic(auth, "admin", admin_hash),
            }
        # Tools protegidas por auth Bearer; documentação pública em GET /mcp/docs.
        # stateless_http: sem sessão no servidor -> restart/deploy NÃO derruba agentes
        # conectados ("Session terminated"); nossas tools são busca pura, sem estado.
        app = BearerAuthMiddleware(
            mcp.http_app(stateless_http=True),
            settings.mcp_tokens_path,
            docs_provider=lambda: build_docs(client, collection, catalog),
            html_renderer=render_docs_html,
            **admin_kwargs,
        )
        uvicorn.run(app, host=args.host, port=args.http)
    else:
        mcp.run()  # stdio (local, sem auth)


if __name__ == "__main__":
    main()
