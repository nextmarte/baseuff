"""Conector dos Boletins de Serviço (``boletimdeservico.uff.br``).

O site (WordPress) organiza os boletins em uma página por ano:
``/boletins/bs-YYYY``. Cada edição aparece como um link cujo **texto** traz o
número e a data (``– 159, de 27/12/2024``) e cujo **href** aponta o PDF
(``.../sites/620/2024/12/159-24.pdf``). O número e a data vêm do texto da âncora,
o que torna o parser robusto à marcação da página.
"""

from __future__ import annotations

import datetime as dt
import re
from urllib.parse import urljoin

from selectolax.parser import HTMLParser
from uff_core.schemas import DocStatus, Document, Source

BASE = "https://boletimdeservico.uff.br"

# Texto da âncora: "– 159, de 27/12/2024" (com marcadores opcionais como (*), (**)).
_EDITION_RE = re.compile(r"(?P<numero>\d+)\s*,\s*de\s*(?P<data>\d{2}/\d{2}/\d{4})")


class BoletimConnector:
    source = Source.BOLETIM

    def __init__(self, start_year: int = 2010, current_year: int | None = None) -> None:
        self.start_year = start_year
        self.current_year = current_year or dt.date.today().year

    def index_urls(self) -> list[str]:
        return [
            f"{BASE}/boletins/bs-{year}" for year in range(self.start_year, self.current_year + 1)
        ]

    def parse_index(self, url: str, html: str) -> list[Document]:
        docs: list[Document] = []
        for anchor in HTMLParser(html).css("a"):
            href = (anchor.attributes.get("href") or "").strip()
            text = (anchor.text() or "").strip()
            if not href or not self._is_pdf(href):
                continue
            match = _EDITION_RE.search(text)
            if not match:
                continue  # link de PDF sem o padrão "N, de DD/MM/AAAA" (ex.: referência cruzada)
            docs.append(self._to_document(url, href, text, match))
        return docs

    # -- helpers ---------------------------------------------------------------

    @staticmethod
    def _is_pdf(href: str) -> bool:
        return href.split("#", 1)[0].lower().endswith(".pdf")

    def _to_document(self, page_url: str, href: str, text: str, match: re.Match[str]) -> Document:
        numero = match.group("numero")
        publish_date = dt.datetime.strptime(match.group("data"), "%d/%m/%Y").date()
        abs_url = urljoin(page_url, href.split("#", 1)[0])
        retificado = "RETIFICADO" in abs_url.upper() or "RETIFICA" in text.upper()
        return Document(
            source=self.source,
            url=abs_url,
            title=f"Boletim de Serviço nº {numero}, de {match.group('data')}",
            numero=numero,
            publish_date=publish_date,
            content_type="application/pdf",
            status=DocStatus.DISCOVERED,
            extra={"ano": publish_date.year, "retificado": retificado},
        )
