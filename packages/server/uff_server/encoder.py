"""Encoder de query remoto: cliente HTTP do microserviço BGE-M3 no host GPU.

Mantém o host de serving (ultron) sem torch: a query é enviada ao endpoint
``/encode`` (skynet01/02), que devolve o vetor denso e os pesos esparsos.
Implementa o protocolo :class:`~uff_server.retriever.QueryEncoder`.
"""

from __future__ import annotations

from collections import OrderedDict

import httpx

from .retriever import QueryVector


class RemoteEncoder:
    def __init__(self, base_url: str, timeout: float = 30.0, cache_size: int = 512) -> None:
        self._client = httpx.Client(base_url=base_url, timeout=timeout)
        self._cache: OrderedDict[str, QueryVector] = OrderedDict()
        self._cache_size = cache_size

    def encode_query(self, text: str) -> QueryVector:
        cached = self._cache.get(text)
        if cached is not None:
            self._cache.move_to_end(text)  # LRU: marca como recém-usado
            return cached
        resp = self._client.post("/encode", json={"text": text})
        resp.raise_for_status()
        data = resp.json()
        qv = QueryVector(
            dense=data["dense"],
            sparse_indices=data["sparse_indices"],
            sparse_values=data["sparse_values"],
        )
        self._cache[text] = qv
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)  # descarta o menos usado
        return qv

    def close(self) -> None:
        self._client.close()
