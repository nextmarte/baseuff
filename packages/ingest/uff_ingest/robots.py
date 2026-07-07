"""Política de robots.txt (educada por padrão).

Busca e cacheia o ``robots.txt`` por host e decide se uma URL pode ser buscada.
Ausência/erro de ``robots.txt`` (404, indisponível) => permite (comportamento
padrão da web). O próprio ``robots.txt`` é buscado sem checagem de robots.
"""

from __future__ import annotations

from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from .fetch import Fetcher


class RobotsPolicy:
    def __init__(self, fetcher: Fetcher, user_agent: str) -> None:
        self._fetcher = fetcher
        self._user_agent = user_agent
        self._cache: dict[str, RobotFileParser] = {}

    async def can_fetch(self, url: str) -> bool:
        parser = await self._parser_for(url)
        return parser.can_fetch(self._user_agent, url)

    async def _parser_for(self, url: str) -> RobotFileParser:
        parts = urlsplit(url)
        host = f"{parts.scheme}://{parts.netloc}"
        if host not in self._cache:
            self._cache[host] = await self._load(host)
        return self._cache[host]

    async def _load(self, host: str) -> RobotFileParser:
        parser = RobotFileParser()
        try:
            result = await self._fetcher.get(f"{host}/robots.txt")
        except httpx.HTTPError:
            parser.parse([])  # indisponível => permite tudo
            return parser
        if result.status_code >= 400:
            parser.parse([])  # 404 etc. => permite tudo
        else:
            parser.parse(result.content.decode("utf-8", errors="replace").splitlines())
        return parser
