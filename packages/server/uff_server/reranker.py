"""Cliente do reranker remoto (endpoint /rerank no host GPU)."""

from __future__ import annotations

from typing import Protocol

import httpx


class Reranker(Protocol):
    def rerank(self, query: str, passages: list[str]) -> list[float]: ...


class RemoteReranker:
    def __init__(self, base_url: str, timeout: float = 60.0) -> None:
        self._client = httpx.Client(base_url=base_url, timeout=timeout)

    def rerank(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        resp = self._client.post("/rerank", json={"query": query, "passages": passages})
        resp.raise_for_status()
        return resp.json()["scores"]

    def close(self) -> None:
        self._client.close()
