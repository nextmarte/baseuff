"""Conector dos Atos Normativos (``atosnormativos.uff.br/listagem-dos-atos/``).

Índice de metadados: o conteúdo dos atos (portarias, resoluções, etc.) mora
dentro dos PDFs do Boletim de Serviço; esta listagem aponta cada ato para o PDF
e a página onde ele aparece (``.../NNN-YY.pdf#page=K``). O conector extrai o mapa
tipo/número/data → PDF+página, enriquecendo a busca sem duplicar conteúdo.
"""

from __future__ import annotations

import datetime as dt
import re
from urllib.parse import urljoin

from selectolax.parser import HTMLParser
from uff_core.schemas import DocStatus, Document, Source

BASE = "https://atosnormativos.uff.br"
LISTAGEM = f"{BASE}/listagem-dos-atos/"

# Tipos de ato reconhecidos (normalizados sem acento/minúsculo para a chave `tipo`).
_TIPOS = {
    "portaria": "portaria",
    "resolução": "resolucao",
    "resolucao": "resolucao",
    "instrução normativa": "instrucao_normativa",
    "instrucao normativa": "instrucao_normativa",
    "instrução de serviço": "instrucao_servico",
    "determinação de serviço": "determinacao_servico",
    "determinação": "determinacao",
    "deliberação": "deliberacao",
    "decisão": "decisao",
    "ordem de serviço": "ordem_servico",
}
_TIPOS_ALT = "|".join(sorted((re.escape(k) for k in _TIPOS), key=len, reverse=True))

_MESES = {
    "janeiro": 1,
    "fevereiro": 2,
    "março": 3,
    "marco": 3,
    "abril": 4,
    "maio": 5,
    "junho": 6,
    "julho": 7,
    "agosto": 8,
    "setembro": 9,
    "outubro": 10,
    "novembro": 11,
    "dezembro": 12,
}

# "Portaria nº 67.542, de 30 de setembro de 2020"
_ATO_RE = re.compile(
    rf"(?P<tipo>{_TIPOS_ALT})\s+n[º°o]?\.?\s*"
    r"(?P<numero>[\d][\d./-]*)\s*,?\s*de\s+"
    r"(?P<dia>\d{1,2})\s+de\s+(?P<mes>[A-Za-zçãéêô]+)\s+de\s+(?P<ano>\d{4})",
    re.IGNORECASE,
)


class AtosNormativosConnector:
    source = Source.RESOLUCAO

    def index_urls(self) -> list[str]:
        return [LISTAGEM]

    def parse_index(self, url: str, html: str) -> list[Document]:
        docs: list[Document] = []
        for anchor in HTMLParser(html).css("a"):
            href = (anchor.attributes.get("href") or "").strip()
            text = (anchor.text() or "").strip()
            if not href or not href.split("#", 1)[0].lower().endswith(".pdf"):
                continue
            match = _ATO_RE.search(text)
            if not match:
                continue
            doc = self._to_document(url, href, text, match)
            if doc is not None:
                docs.append(doc)
        return docs

    def _to_document(
        self, page_url: str, href: str, text: str, match: re.Match[str]
    ) -> Document | None:
        mes = _MESES.get(match.group("mes").lower())
        if mes is None:
            return None
        publish_date = dt.date(int(match.group("ano")), mes, int(match.group("dia")))
        abs_url = urljoin(page_url, href)
        page = self._page_anchor(abs_url)
        return Document(
            source=self.source,
            url=abs_url,
            title=text,
            numero=match.group("numero"),
            publish_date=publish_date,
            content_type="application/pdf",
            status=DocStatus.DISCOVERED,
            extra={
                "tipo": _TIPOS[match.group("tipo").lower()],
                "boletim_ref": abs_url.split("#", 1)[0],
                "page": page,
            },
        )

    @staticmethod
    def _page_anchor(url: str) -> int | None:
        m = re.search(r"#page=(\d+)", url)
        return int(m.group(1)) if m else None
