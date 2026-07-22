"""Sincroniza o índice do ultron para o Volume da réplica Modal (best-effort).

Empurra para o Volume ``baseuff-data``: snapshot do Qdrant, catálogo (backup
consistente), tokens dos agentes, hash do admin e um manifest. Roda no fim do
``update.py`` diário (falha NÃO aborta o update) ou manualmente:

    uv run python scripts/sync_replica.py

Sem a CLI da Modal instalada/logada, apenas avisa e sai com código 0 — assim o
cron do ultron funciona igual antes de a réplica existir. A réplica em si fica
DESARMADA por padrão (``scripts/replica.sh``); sincronizar não sobe container
nem gasta compute — só reescreve arquivos no Volume (centavos de storage).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parent.parent
VOLUME = "baseuff-data"
COLLECTION = "uff_chunks"
QDRANT_URL = os.environ.get("UFF_QDRANT_URL", "http://localhost:6333")
# cron tem PATH mínimo: resolver a CLI por caminho absoluto (mesma pegadinha do uv)
MODAL = shutil.which("modal") or os.path.expanduser("~/.local/bin/modal")
STAGING = REPO / "data" / "replica_sync"


def log(msg: str) -> None:
    print(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} [sync_replica] {msg}", flush=True)


def criar_snapshot(base_url: str) -> str:
    """Cria um snapshot da coleção no Qdrant e devolve o nome dele."""
    resp = httpx.post(
        f"{base_url}/collections/{COLLECTION}/snapshots",
        params={"wait": "true"},
        timeout=1800,
    )
    resp.raise_for_status()
    return resp.json()["result"]["name"]


def baixar_snapshot(base_url: str, nome: str, destino: Path) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    with httpx.stream(
        "GET", f"{base_url}/collections/{COLLECTION}/snapshots/{nome}", timeout=1800
    ) as resp:
        resp.raise_for_status()
        with open(destino, "wb") as f:
            for chunk in resp.iter_bytes(1 << 20):
                f.write(chunk)


def apagar_snapshot(base_url: str, nome: str) -> None:
    """Remove o snapshot do lado do Qdrant (não deixar GBs acumulando no docker)."""
    httpx.delete(
        f"{base_url}/collections/{COLLECTION}/snapshots/{nome}", timeout=300
    ).raise_for_status()


def backup_catalogo(destino: Path) -> None:
    """Backup consistente do SQLite (mesmo mecanismo do update.py/embed)."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["sqlite3", str(REPO / "data" / "catalog.db"), f".backup {destino}"], check=True)


def montar_manifest(pontos: int, arquivos: dict[str, Path], quando: dt.datetime) -> dict:
    return {
        "quando": quando.isoformat(timespec="seconds"),
        "collection": COLLECTION,
        "points": pontos,
        "arquivos": {
            nome: caminho.stat().st_size for nome, caminho in arquivos.items() if caminho.exists()
        },
    }


def contar_pontos(base_url: str) -> int:
    resp = httpx.get(f"{base_url}/collections/{COLLECTION}", timeout=30)
    resp.raise_for_status()
    return int(resp.json()["result"]["points_count"])


def volume_put(local: Path, remoto: str, executar=subprocess.run) -> None:
    executar(
        [MODAL, "volume", "put", VOLUME, str(local), remoto, "--force"],
        check=True,
    )


def main() -> int:
    if not Path(MODAL).exists():
        log(
            "CLI da modal não encontrada — pulando sync da réplica (instale com "
            "`uv tool install modal` e rode `modal setup` para habilitar)"
        )
        return 0

    log(f"criando snapshot de {COLLECTION} em {QDRANT_URL}")
    nome = criar_snapshot(QDRANT_URL)
    snap_local = STAGING / f"{COLLECTION}.snapshot"
    try:
        baixar_snapshot(QDRANT_URL, nome, snap_local)
    finally:
        apagar_snapshot(QDRANT_URL, nome)
    log(f"snapshot baixado ({snap_local.stat().st_size / 1e9:.2f} GB)")

    catalogo = STAGING / "catalog.db"
    backup_catalogo(catalogo)

    tokens = REPO / "data" / "mcp_tokens.txt"
    admin_hash = REPO / "data" / "admin_pass.hash"
    manifest = STAGING / "manifest.json"
    manifest.write_text(
        json.dumps(
            montar_manifest(
                contar_pontos(QDRANT_URL),
                {"snapshot": snap_local, "catalog": catalogo, "tokens": tokens},
                dt.datetime.now(),
            ),
            ensure_ascii=False,
            indent=2,
        )
    )

    log(f"enviando para o Volume {VOLUME}")
    volume_put(snap_local, f"/snapshots/{COLLECTION}.snapshot")
    volume_put(catalogo, "/catalog.db")
    if tokens.exists():
        volume_put(tokens, "/config/mcp_tokens.txt")
    if admin_hash.exists():
        volume_put(admin_hash, "/config/admin_pass.hash")
    volume_put(manifest, "/manifest.json")

    snap_local.unlink(missing_ok=True)  # não deixar GBs no disco do ultron
    log("sync concluído")
    return 0


if __name__ == "__main__":
    sys.exit(main())
