"""Protocolo comum dos conectores.

Um conector separa **descoberta pura** (parse de páginas de índice → lista de
``Document``, testável sobre fixtures, sem rede) da camada de IO (o crawler,
que busca as páginas e persiste no catálogo). ``index_urls`` diz *quais* páginas
varrer; ``parse_index`` transforma o HTML de uma delas em documentos descobertos.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from uff_core.schemas import Document, Source


@runtime_checkable
class Connector(Protocol):
    """Contrato de descoberta de uma fonte."""

    source: Source

    def index_urls(self) -> list[str]:
        """URLs das páginas de índice a varrer para descobrir documentos."""
        ...

    def parse_index(self, url: str, html: str) -> list[Document]:
        """Extrai documentos (status DISCOVERED) do HTML de uma página de índice."""
        ...
