"""Orquestrador de atualização incremental do BaseUFF (roda no ultron, via cron).

Encadeia, para as fontes pedidas: descobrir novos -> baixar o delta -> rsync p/ os
hosts GPU -> embed (run_batch pula o que já está no Qdrant) -> índice atualizado.

É incremental por construção: a descoberta deduplica, o download só pega DISCOVERED
e o embed pula documentos já indexados. Uma trava (lock) impede execuções sobrepostas.

    uv run python scripts/update.py --sources boletim,pesquisa      # job diário
    uv run python scripts/update.py --sources atos,sti_kb           # job semanal
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# O cron tem PATH mínimo (sem ~/.local/bin), então resolvemos o `uv` absoluto: chamar
# só "uv" no subprocess falha com FileNotFoundError sob cron.
UV = shutil.which("uv") or os.path.expanduser("~/.local/bin/uv")
KEY = os.path.expanduser("~/.ssh/id_ed25519_baseuff")
HOST = "marcus@cid-uff.net"
# (porta ssh, shard) — só skynet01 (skynet02 fica livre para outros serviços)
GPU_HOSTS = [("22023", 0)]
QDRANT_URL = "http://10.171.69.1:6333"
LOCK = REPO / "data" / ".update.lock"


def log(msg: str) -> None:
    print(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} [update] {msg}", flush=True)


def sh(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    log("$ " + " ".join(cmd))
    return subprocess.run(cmd, check=True, **kw)


def rsync(port: str, src: str, dst: str) -> None:
    rsh = f"ssh -i {KEY} -p {port} -o BatchMode=yes"
    sh(["rsync", "-az", "-e", rsh, src, f"{HOST}:{dst}"])


def ssh(port: str, remote_cmd: str) -> subprocess.CompletedProcess:
    return sh(
        ["ssh", "-i", KEY, "-p", port, "-o", "BatchMode=yes", HOST, remote_cmd],
    )


def acquire_lock() -> None:
    if LOCK.exists():
        pid = LOCK.read_text().strip()
        # trava obsoleta se o processo dono não existe mais
        if pid and Path(f"/proc/{pid}").exists():
            log(f"outra atualização em curso (pid {pid}); saindo")
            sys.exit(0)
        log("removendo lock obsoleto")
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    LOCK.write_text(str(os.getpid()))


def release_lock() -> None:
    LOCK.unlink(missing_ok=True)


def ingest(source: str) -> None:
    """Descoberta + download do delta de uma fonte (no ultron)."""
    if source == "guia":
        # Tutoriais do estudante (www.uff.br via REST); idempotente, pula o já baixado.
        sh([UV, "run", "python", "scripts/crawl_guia.py"], cwd=REPO)
        return
    if source == "sbpc":
        # 78ª RA da SBPC: programação/minicursos/sites/notícias/PDFs; checksum + purge
        # no Qdrant quando um doc já indexado muda (a programação é viva até o evento).
        sh([UV, "run", "python", "scripts/crawl_sbpc.py"], cwd=REPO)
        return
    if source == "sti_kb":
        sh([UV, "run", "--with", "playwright", "python", "scripts/crawl_citsmart.py"], cwd=REPO)
        sh(
            [
                UV,
                "run",
                "--with",
                "rapidocr-onnxruntime",
                "--with",
                "pillow",
                "--with",
                "numpy",
                "python",
                "scripts/enrich_sti_kb.py",
            ],
            cwd=REPO,
        )
        return
    sh([UV, "run", "python", "scripts/crawl.py", "--source", source], cwd=REPO)
    if source != "atos":  # atos é só índice de metadados (não baixa binários)
        sh([UV, "run", "python", "scripts/download.py", "--source", source], cwd=REPO)


def embed(sources: list[str]) -> None:
    """rsync do delta + embed nos hosts GPU (sharded). run_batch pula o já indexado."""
    data = REPO / "data"
    subprocess.run(
        ["sqlite3", str(data / "catalog.db"), f".backup {data}/catalog-snapshot.db"], check=True
    )
    for port, shard in GPU_HOSTS:
        ssh(port, "mkdir -p ~/baseuff-worker/data/raw")
        rsync(port, f"{data}/catalog-snapshot.db", "baseuff-worker/data/catalog.db")
        for source in sources:
            src_dir = data / "raw" / source
            if src_dir.exists():
                ssh(port, f"mkdir -p ~/baseuff-worker/data/raw/{source}")
                rsync(port, f"{src_dir}/", f"baseuff-worker/data/raw/{source}/")
        src_arg = ",".join(sources)
        ssh(
            port,
            f"cd ~/baseuff-worker/embed && uv run python run_batch.py "
            f"--data ../data --qdrant-url {QDRANT_URL} --shard {shard} "
            f"--num-shards {len(GPU_HOSTS)} --sources {src_arg}",
        )


def sync_replica() -> None:
    """Empurra snapshot/catálogo p/ o Volume da réplica Modal (best-effort).

    Nunca aborta o update: sem CLI da modal o script já sai 0; qualquer outra
    falha (rede, Qdrant) é logada e engolida — a réplica só fica com o sync anterior.
    """
    try:
        sh([UV, "run", "python", "scripts/sync_replica.py"], cwd=REPO)
    except Exception as exc:
        log(f"sync da réplica falhou (não-fatal): {exc}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Atualização incremental do BaseUFF")
    ap.add_argument("--sources", required=True, help="ex.: boletim,pesquisa")
    ap.add_argument("--skip-embed", action="store_true", help="só descobrir/baixar (sem GPU)")
    args = ap.parse_args()
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]

    acquire_lock()
    try:
        log(f"início — fontes: {sources}")
        for source in sources:
            ingest(source)
        if not args.skip_embed:
            embed([s for s in sources if s != "atos"])
        sync_replica()
        log("fim — índice atualizado")
    finally:
        release_lock()


if __name__ == "__main__":
    main()
