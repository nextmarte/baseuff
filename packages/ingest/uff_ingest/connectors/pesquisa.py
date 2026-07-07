"""Conector do Portal da Pesquisa (``pesquisa.uff.br`` — Drupal).

Notícias/editais (PIBIC, chamadas internas, bolsas) paginados em
``/?q=node&page=N`` (~9 artigos por página). Cada artigo vive em
``/?q=content/slug`` (HTML server-side). Os títulos ficam em âncoras de ``h2``;
os links "Leia mais" duplicados são ignorados pela seleção estrutural.
"""

from __future__ import annotations

from urllib.parse import urljoin

from selectolax.parser import HTMLParser
from uff_core.schemas import DocStatus, Document, Source

BASE = "https://pesquisa.uff.br"


class PesquisaConnector:
    source = Source.PESQUISA

    def __init__(self, max_pages: int = 150) -> None:
        # Hoje há ~109 páginas; a folga cobre crescimento (páginas vazias são inócuas).
        self.max_pages = max_pages

    def index_urls(self) -> list[str]:
        return [f"{BASE}/?q=node&page={page}" for page in range(self.max_pages)]

    def parse_index(self, url: str, html: str) -> list[Document]:
        docs: list[Document] = []
        seen: set[str] = set()
        for anchor in HTMLParser(html).css("h2 a"):
            href = (anchor.attributes.get("href") or "").strip()
            title = (anchor.text() or "").strip()
            if "content/" not in href or not title:
                continue
            abs_url = urljoin(url, href)
            if abs_url in seen:
                continue
            seen.add(abs_url)
            docs.append(
                Document(
                    source=self.source,
                    url=abs_url,
                    title=title,
                    content_type="text/html",
                    status=DocStatus.DISCOVERED,
                )
            )
        return docs
