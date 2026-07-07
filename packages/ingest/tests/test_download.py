import hashlib

import httpx
import pytest
import respx
from uff_core.catalog import Catalog
from uff_core.schemas import DocStatus, Document, Source
from uff_ingest.download import fetch_documents
from uff_ingest.fetch import Fetcher

pytestmark = pytest.mark.asyncio

PDF = b"%PDF-1.4 conteudo do boletim"


def _fetcher(client):
    return Fetcher(client, user_agent="t", requests_per_second=1000.0, retry_wait=0.0)


@respx.mock(assert_all_called=False)
async def test_downloads_saves_file_and_updates_catalog(tmp_path, respx_mock):
    cat = Catalog(":memory:")
    doc = cat.upsert(Document(source=Source.BOLETIM, url="https://x.uff.br/1.pdf", numero="1"))
    respx_mock.get("https://x.uff.br/1.pdf").mock(
        return_value=httpx.Response(
            200, content=PDF, headers={"Content-Type": "application/pdf", "ETag": 'W/"e1"'}
        )
    )
    async with httpx.AsyncClient() as client:
        report = await fetch_documents(cat, _fetcher(client), data_dir=str(tmp_path))

    assert report.fetched == 1
    updated = cat.get(doc.id)
    assert updated.status is DocStatus.FETCHED
    assert updated.checksum == hashlib.sha256(PDF).hexdigest()
    assert updated.etag == 'W/"e1"'
    saved = tmp_path / "raw" / "boletim" / f"{doc.id}.pdf"
    assert saved.exists() and saved.read_bytes() == PDF
    cat.close()


@respx.mock(assert_all_called=False)
async def test_only_fetches_discovered_and_is_idempotent(tmp_path, respx_mock):
    cat = Catalog(":memory:")
    doc = cat.upsert(Document(source=Source.BOLETIM, url="https://x.uff.br/1.pdf"))
    respx_mock.get("https://x.uff.br/1.pdf").mock(
        return_value=httpx.Response(200, content=PDF, headers={"Content-Type": "application/pdf"})
    )
    async with httpx.AsyncClient() as client:
        first = await fetch_documents(cat, _fetcher(client), data_dir=str(tmp_path))
        second = await fetch_documents(cat, _fetcher(client), data_dir=str(tmp_path))
    assert first.fetched == 1
    assert second.fetched == 0  # já FETCHED, não rebaixa
    assert cat.get(doc.id).status is DocStatus.FETCHED
    cat.close()


@respx.mock(assert_all_called=False)
async def test_limit_and_source_filter(tmp_path, respx_mock):
    cat = Catalog(":memory:")
    cat.upsert(Document(source=Source.BOLETIM, url="https://x.uff.br/a.pdf"))
    cat.upsert(Document(source=Source.BOLETIM, url="https://x.uff.br/b.pdf"))
    cat.upsert(Document(source=Source.RESOLUCAO, url="https://x.uff.br/c.pdf"))
    respx_mock.get(url__regex=r"https://x\.uff\.br/.*\.pdf").mock(
        return_value=httpx.Response(200, content=PDF, headers={"Content-Type": "application/pdf"})
    )
    async with httpx.AsyncClient() as client:
        report = await fetch_documents(
            cat, _fetcher(client), data_dir=str(tmp_path), source=Source.BOLETIM, limit=1
        )
    assert report.fetched == 1  # só 1 (limit), e apenas da fonte boletim
    cat.close()


@respx.mock(assert_all_called=False)
async def test_http_error_marks_error_and_continues(tmp_path, respx_mock):
    cat = Catalog(":memory:")
    bad = cat.upsert(Document(source=Source.BOLETIM, url="https://x.uff.br/bad.pdf"))
    good = cat.upsert(Document(source=Source.BOLETIM, url="https://x.uff.br/good.pdf"))
    respx_mock.get("https://x.uff.br/bad.pdf").mock(return_value=httpx.Response(404))
    respx_mock.get("https://x.uff.br/good.pdf").mock(
        return_value=httpx.Response(200, content=PDF, headers={"Content-Type": "application/pdf"})
    )
    async with httpx.AsyncClient() as client:
        report = await fetch_documents(cat, _fetcher(client), data_dir=str(tmp_path))
    assert report.fetched == 1
    assert report.errors == 1
    assert cat.get(bad.id).status is DocStatus.ERROR
    assert cat.get(good.id).status is DocStatus.FETCHED
    cat.close()
