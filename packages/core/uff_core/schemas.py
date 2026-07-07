"""Schemas do domínio BaseUFF.

Modelos leves (Pydantic) usados por toda a pipeline: descrevem um documento do
acervo e seu ciclo de vida no catálogo, além dos chunks derivados.
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Source(StrEnum):
    """Fontes abertas da UFF que raspamos."""

    BOLETIM = "boletim"
    RESOLUCAO = "resolucao"
    STI_MANUAL = "sti_manual"
    STI_KB = "sti_kb"
    PESQUISA = "pesquisa"


class DocStatus(StrEnum):
    """Ciclo de vida de um documento no catálogo."""

    DISCOVERED = "discovered"  # descoberto no índice, ainda não baixado
    FETCHED = "fetched"  # binário/HTML baixado em raw/
    PARSED = "parsed"  # convertido para Markdown+metadados
    PENDING_EMBED = "pending_embed"  # chunkado, aguardando vetorização
    INDEXED = "indexed"  # vetores no Qdrant
    ERROR = "error"  # falha em alguma etapa


class Document(BaseModel):
    """Um documento do acervo (unidade de rastreamento do catálogo).

    A chave natural para deduplicação é (``source``, ``url``); ``checksum``,
    ``etag`` e ``last_modified`` habilitam ingestão incremental.
    """

    id: int | None = None
    source: Source
    url: str
    title: str | None = None
    numero: str | None = None
    publish_date: dt.date | None = None
    orgao: str | None = None
    content_type: str | None = None
    checksum: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    status: DocStatus = DocStatus.DISCOVERED
    extra: dict[str, Any] = Field(default_factory=dict)
