"""Camada de fetch polida para o crawler.

``Fetcher`` encapsula o HTTP: user-agent identificável, GET condicional
(``If-None-Match``/``If-Modified-Since`` para ingestão incremental), retry com
backoff em erros transitórios (429/5xx e falhas de transporte) e rate-limit por
host. É a única parte que toca a rede; a lógica de descoberta (parse) permanece
pura e testável à parte.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_fixed,
)

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


@dataclass
class FetchResult:
    url: str
    status_code: int
    content: bytes
    headers: httpx.Headers

    @property
    def not_modified(self) -> bool:
        return self.status_code == 304

    @property
    def etag(self) -> str | None:
        return self.headers.get("ETag")

    @property
    def last_modified(self) -> str | None:
        return self.headers.get("Last-Modified")

    @property
    def content_type(self) -> str | None:
        return self.headers.get("Content-Type")


class _RetryableStatus(Exception):
    """Status transitório que deve disparar retry; carrega a resposta."""

    def __init__(self, response: httpx.Response) -> None:
        self.response = response


class Fetcher:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        user_agent: str,
        requests_per_second: float = 1.0,
        max_attempts: int = 4,
        retry_wait: float = 0.5,
    ) -> None:
        self._client = client
        self._user_agent = user_agent
        self._min_interval = 1.0 / requests_per_second if requests_per_second > 0 else 0.0
        self._max_attempts = max_attempts
        self._retry_wait = retry_wait
        self._host_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._host_last: dict[str, float] = {}

    async def get(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> FetchResult:
        headers = {"User-Agent": self._user_agent}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        try:
            async for attempt in AsyncRetrying(
                retry=retry_if_exception_type((_RetryableStatus, httpx.TransportError)),
                stop=stop_after_attempt(self._max_attempts),
                wait=wait_fixed(self._retry_wait),
                reraise=True,
            ):
                with attempt:
                    await self._throttle(url)
                    resp = await self._client.get(url, headers=headers, follow_redirects=True)
                    if resp.status_code in _RETRYABLE_STATUS:
                        raise _RetryableStatus(resp)
                    return FetchResult(
                        url=str(resp.url),
                        status_code=resp.status_code,
                        content=resp.content,
                        headers=resp.headers,
                    )
        except _RetryableStatus as exhausted:
            exhausted.response.raise_for_status()  # -> httpx.HTTPStatusError
            raise  # defensivo: raise_for_status sempre levanta em 4xx/5xx
        raise AssertionError("unreachable")  # pragma: no cover

    async def _throttle(self, url: str) -> None:
        if self._min_interval <= 0:
            return
        host = httpx.URL(url).host
        async with self._host_locks[host]:
            loop = asyncio.get_event_loop()
            wait = self._host_last.get(host, 0.0) + self._min_interval - loop.time()
            if wait > 0:
                await asyncio.sleep(wait)
            self._host_last[host] = loop.time()
