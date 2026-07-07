"""Crawler de descoberta.

Dirige um :class:`~uff_ingest.connectors.base.Connector`: para cada página de
índice, respeita o robots.txt, busca o HTML e persiste os documentos descobertos
no catálogo (deduplicando por chave natural). Não baixa os binários — isso é uma
etapa separada e mais pesada (``fetch_documents``), gated à parte.
"""

from __future__ import annotations

from dataclasses import dataclass

from uff_core.catalog import Catalog

from .connectors.base import Connector
from .fetch import Fetcher
from .robots import RobotsPolicy


@dataclass
class DiscoveryReport:
    pages: int = 0
    discovered: int = 0
    new: int = 0
    skipped_robots: int = 0


class Crawler:
    def __init__(
        self,
        fetcher: Fetcher,
        catalog: Catalog,
        *,
        user_agent: str,
        robots: RobotsPolicy | None = None,
    ) -> None:
        self._fetcher = fetcher
        self._catalog = catalog
        self._robots = robots or RobotsPolicy(fetcher, user_agent)

    async def discover(self, connector: Connector) -> DiscoveryReport:
        report = DiscoveryReport()
        for url in connector.index_urls():
            if not await self._robots.can_fetch(url):
                report.skipped_robots += 1
                continue
            result = await self._fetcher.get(url)
            report.pages += 1
            html = result.content.decode("utf-8", errors="replace")
            for doc in connector.parse_index(result.url, html):
                is_new = self._catalog.get_by_url(doc.source, doc.url) is None
                self._catalog.upsert(doc)
                report.discovered += 1
                report.new += int(is_new)
        return report
