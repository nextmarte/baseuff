"""Download dos binários descobertos (etapa pesada, separada da descoberta).

Baixa os documentos com status ``DISCOVERED``, salva no raw store
(``{data_dir}/raw/{source}/{doc_id}.{ext}``), calcula o checksum SHA-256 e move o
status para ``FETCHED`` (ou ``ERROR``). Idempotente: só toca documentos
``DISCOVERED``, então re-executar não rebaixa nem rebaixa o que já foi buscado.
"""

from __future__ import annotations

import hashlib
import pathlib
from dataclasses import dataclass

import httpx
from uff_core.catalog import Catalog
from uff_core.schemas import DocStatus, Document, Source

from .fetch import Fetcher

_EXT_BY_CONTENT_TYPE = {
    "application/pdf": "pdf",
    "text/html": "html",
    "application/msword": "doc",
}


@dataclass
class FetchReport:
    fetched: int = 0
    skipped: int = 0
    errors: int = 0
    bytes: int = 0


def _extension(doc: Document, content_type: str | None) -> str:
    ct = (content_type or doc.content_type or "").split(";", 1)[0].strip().lower()
    if ct in _EXT_BY_CONTENT_TYPE:
        return _EXT_BY_CONTENT_TYPE[ct]
    suffix = pathlib.Path(doc.url.split("#", 1)[0].split("?", 1)[0]).suffix.lstrip(".")
    return suffix.lower() or "bin"


async def fetch_documents(
    catalog: Catalog,
    fetcher: Fetcher,
    *,
    data_dir: str,
    source: Source | None = None,
    limit: int | None = None,
) -> FetchReport:
    report = FetchReport()
    pending = catalog.list_by_status(DocStatus.DISCOVERED)
    if source is not None:
        pending = [d for d in pending if d.source is source]
    if limit is not None:
        pending = pending[:limit]

    for doc in pending:
        assert doc.id is not None
        try:
            result = await fetcher.get(doc.url, etag=doc.etag, last_modified=doc.last_modified)
        except httpx.HTTPError:
            catalog.record_fetch(doc.id, status=DocStatus.ERROR)
            report.errors += 1
            continue

        if result.status_code >= 400:
            catalog.record_fetch(doc.id, status=DocStatus.ERROR)
            report.errors += 1
            continue
        if result.not_modified:
            report.skipped += 1
            continue

        ext = _extension(doc, result.content_type)
        dest = pathlib.Path(data_dir) / "raw" / doc.source.value / f"{doc.id}.{ext}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(result.content)

        catalog.record_fetch(
            doc.id,
            status=DocStatus.FETCHED,
            checksum=hashlib.sha256(result.content).hexdigest(),
            etag=result.etag,
            last_modified=result.last_modified,
            content_type=result.content_type,
        )
        report.fetched += 1
        report.bytes += len(result.content)

    return report
