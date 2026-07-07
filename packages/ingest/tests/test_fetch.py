import httpx
import pytest
import respx
from uff_ingest.fetch import Fetcher

pytestmark = pytest.mark.asyncio


async def _fetcher(client: httpx.AsyncClient) -> Fetcher:
    # rps alto e sem espera de retry para testes rápidos e determinísticos.
    return Fetcher(
        client, user_agent="BaseUFF-test/0.1", requests_per_second=1000.0, retry_wait=0.0
    )


@respx.mock
async def test_get_returns_content_and_metadata():
    respx.get("https://x.uff.br/a.pdf").mock(
        return_value=httpx.Response(200, content=b"%PDF-1.4", headers={"ETag": 'W/"abc"'})
    )
    async with httpx.AsyncClient() as client:
        r = await (await _fetcher(client)).get("https://x.uff.br/a.pdf")
    assert r.status_code == 200
    assert r.content == b"%PDF-1.4"
    assert r.etag == 'W/"abc"'
    assert r.not_modified is False


@respx.mock
async def test_sends_user_agent_and_conditional_headers():
    route = respx.get("https://x.uff.br/a").mock(return_value=httpx.Response(200, content=b"ok"))
    async with httpx.AsyncClient() as client:
        await (await _fetcher(client)).get(
            "https://x.uff.br/a", etag='W/"abc"', last_modified="Wed, 01 Jan 2025 00:00:00 GMT"
        )
    sent = route.calls.last.request
    assert sent.headers["User-Agent"] == "BaseUFF-test/0.1"
    assert sent.headers["If-None-Match"] == 'W/"abc"'
    assert sent.headers["If-Modified-Since"] == "Wed, 01 Jan 2025 00:00:00 GMT"


@respx.mock
async def test_304_marks_not_modified():
    respx.get("https://x.uff.br/a").mock(return_value=httpx.Response(304))
    async with httpx.AsyncClient() as client:
        r = await (await _fetcher(client)).get("https://x.uff.br/a", etag='W/"abc"')
    assert r.status_code == 304
    assert r.not_modified is True


@respx.mock
async def test_retries_on_503_then_succeeds():
    route = respx.get("https://x.uff.br/a").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, content=b"ok")]
    )
    async with httpx.AsyncClient() as client:
        r = await (await _fetcher(client)).get("https://x.uff.br/a")
    assert r.status_code == 200
    assert route.call_count == 2


@respx.mock
async def test_gives_up_after_max_attempts():
    respx.get("https://x.uff.br/a").mock(return_value=httpx.Response(503))
    async with httpx.AsyncClient() as client:
        f = Fetcher(
            client, user_agent="t", requests_per_second=1000.0, retry_wait=0.0, max_attempts=3
        )
        with pytest.raises(httpx.HTTPStatusError):
            await f.get("https://x.uff.br/a")
