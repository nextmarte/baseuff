"""CLI de download: baixa binários de documentos DISCOVERED para o raw store.

uv run python scripts/download.py --source boletim --limit 5
"""

from __future__ import annotations

import argparse
import asyncio

import httpx
from uff_core.catalog import Catalog
from uff_core.config import Settings, sqlite_path
from uff_core.schemas import Source
from uff_ingest.download import fetch_documents
from uff_ingest.fetch import Fetcher


async def run(source: str | None, limit: int | None) -> None:
    settings = Settings()
    catalog = Catalog(sqlite_path(settings.catalog_dsn))
    src = Source(source) if source else None

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
        fetcher = Fetcher(
            client,
            user_agent=settings.user_agent,
            requests_per_second=settings.requests_per_second,
        )
        print(f"[download] fonte={source or 'todas'} limite={limit} ...")
        report = await fetch_documents(
            catalog, fetcher, data_dir=settings.data_dir, source=src, limit=limit
        )

    mb = report.bytes / 1_048_576
    print(
        f"[download] baixados={report.fetched} pulados={report.skipped} "
        f"erros={report.errors} ({mb:.1f} MB) -> {settings.data_dir}/raw/"
    )
    catalog.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Download de binários do acervo UFF")
    parser.add_argument("--source", default=None, help="fonte (boletim, resolucao, ...)")
    parser.add_argument("--limit", type=int, default=None, help="máximo de documentos")
    args = parser.parse_args()
    asyncio.run(run(args.source, args.limit))


if __name__ == "__main__":
    main()
