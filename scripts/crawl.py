"""CLI de descoberta: varre as páginas de índice de uma fonte e popula o catálogo.

Não baixa binários (PDFs) — apenas descobre e registra os documentos. Uso:

    uv run python scripts/crawl.py --source boletim
    uv run python scripts/crawl.py --source boletim --start-year 2024
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib

import httpx
from uff_core.catalog import Catalog
from uff_core.config import Settings, sqlite_path
from uff_ingest.connectors.atos import AtosNormativosConnector
from uff_ingest.connectors.base import Connector
from uff_ingest.connectors.boletim import BoletimConnector
from uff_ingest.connectors.pesquisa import PesquisaConnector
from uff_ingest.crawler import Crawler
from uff_ingest.fetch import Fetcher


def build_connector(source: str, settings: Settings, start_year: int | None) -> Connector:
    if source == "boletim":
        return BoletimConnector(start_year=start_year or settings.boletim_start_year)
    if source in ("atos", "resolucoes"):
        return AtosNormativosConnector()
    if source == "pesquisa":
        return PesquisaConnector()
    raise SystemExit(f"fonte não suportada ainda: {source!r}")


async def run(source: str, start_year: int | None) -> None:
    settings = Settings()
    db_path = sqlite_path(settings.catalog_dsn)
    pathlib.Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    catalog = Catalog(db_path)
    connector = build_connector(source, settings, start_year)

    timeout = httpx.Timeout(30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        fetcher = Fetcher(
            client,
            user_agent=settings.user_agent,
            requests_per_second=settings.requests_per_second,
        )
        crawler = Crawler(fetcher, catalog, user_agent=settings.user_agent)
        print(f"[crawl] fonte={source} descobrindo em {len(connector.index_urls())} páginas...")
        report = await crawler.discover(connector)

    print(
        f"[crawl] páginas={report.pages} descobertos={report.discovered} "
        f"novos={report.new} bloqueados_robots={report.skipped_robots}"
    )
    print(f"[crawl] total no catálogo: {catalog.count()} documentos ({db_path})")
    catalog.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Descoberta de documentos da UFF")
    parser.add_argument("--source", default="boletim", help="fonte (ex.: boletim)")
    parser.add_argument("--start-year", type=int, default=None, help="ano inicial (Boletim)")
    args = parser.parse_args()
    asyncio.run(run(args.source, args.start_year))


if __name__ == "__main__":
    main()
