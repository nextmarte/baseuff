import httpx
import pytest
import respx
from uff_core.catalog import Catalog
from uff_core.schemas import DocStatus
from uff_ingest.connectors.boletim import BoletimConnector
from uff_ingest.crawler import Crawler
from uff_ingest.fetch import Fetcher

pytestmark = pytest.mark.asyncio

YEAR_HTML = """<!doctype html><html><body>
<a href="https://boletimdeservico.uff.br/wp-content/uploads/sites/620/2024/12/159-24.pdf">– 159, de 27/12/2024</a>
<a href="https://boletimdeservico.uff.br/wp-content/uploads/sites/620/2024/11/148-24.pdf">– 148, de 29/11/2024</a>
</body></html>"""

ROBOTS_ALLOW = "User-agent: *\nAllow: /\n"
ROBOTS_BLOCK = "User-agent: *\nDisallow: /boletins/\n"
INDEX_URL = "https://boletimdeservico.uff.br/boletins/bs-2024"
ROBOTS_URL = "https://boletimdeservico.uff.br/robots.txt"


async def _crawler(client, catalog):
    fetcher = Fetcher(client, user_agent="BaseUFF-test", requests_per_second=1000.0, retry_wait=0.0)
    return Crawler(fetcher, catalog, user_agent="BaseUFF-test")


@respx.mock(assert_all_called=False)
async def test_discover_populates_catalog(respx_mock):
    respx_mock.get(ROBOTS_URL).mock(return_value=httpx.Response(200, text=ROBOTS_ALLOW))
    respx_mock.get(INDEX_URL).mock(return_value=httpx.Response(200, text=YEAR_HTML))
    cat = Catalog(":memory:")
    async with httpx.AsyncClient() as client:
        crawler = await _crawler(client, cat)
        report = await crawler.discover(BoletimConnector(start_year=2024, current_year=2024))
    assert report.pages == 1
    assert report.discovered == 2
    assert report.new == 2
    assert cat.count() == 2
    assert {d.numero for d in cat.list_by_status(DocStatus.DISCOVERED)} == {"159", "148"}
    cat.close()


@respx.mock(assert_all_called=False)
async def test_discover_respects_robots_disallow(respx_mock):
    respx_mock.get(ROBOTS_URL).mock(return_value=httpx.Response(200, text=ROBOTS_BLOCK))
    idx = respx_mock.get(INDEX_URL).mock(return_value=httpx.Response(200, text=YEAR_HTML))
    cat = Catalog(":memory:")
    async with httpx.AsyncClient() as client:
        crawler = await _crawler(client, cat)
        report = await crawler.discover(BoletimConnector(start_year=2024, current_year=2024))
    assert report.skipped_robots == 1
    assert report.discovered == 0
    assert idx.call_count == 0  # não tocou a página proibida
    assert cat.count() == 0
    cat.close()


@respx.mock(assert_all_called=False)
async def test_discover_is_idempotent(respx_mock):
    respx_mock.get(ROBOTS_URL).mock(return_value=httpx.Response(200, text=ROBOTS_ALLOW))
    respx_mock.get(INDEX_URL).mock(return_value=httpx.Response(200, text=YEAR_HTML))
    cat = Catalog(":memory:")
    async with httpx.AsyncClient() as client:
        crawler = await _crawler(client, cat)
        conn = BoletimConnector(start_year=2024, current_year=2024)
        first = await crawler.discover(conn)
        second = await crawler.discover(conn)
    assert first.new == 2
    assert second.new == 0  # dedup: nada novo na segunda passada
    assert second.discovered == 2
    assert cat.count() == 2
    cat.close()


@respx.mock(assert_all_called=False)
async def test_discover_allows_when_robots_absent(respx_mock):
    respx_mock.get(ROBOTS_URL).mock(return_value=httpx.Response(404))
    respx_mock.get(INDEX_URL).mock(return_value=httpx.Response(200, text=YEAR_HTML))
    cat = Catalog(":memory:")
    async with httpx.AsyncClient() as client:
        crawler = await _crawler(client, cat)
        report = await crawler.discover(BoletimConnector(start_year=2024, current_year=2024))
    assert report.discovered == 2
    cat.close()
