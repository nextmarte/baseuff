"""Encoder de query remoto: cliente HTTP do microserviço BGE-M3 no host GPU.

Mantém o host de serving (ultron) sem torch: a query é enviada ao endpoint
``/encode`` (skynet01/02), que devolve o vetor denso e os pesos esparsos.
Implementa o protocolo :class:`~uff_server.retriever.QueryEncoder`.
"""

from __future__ import annotations

import httpx

from .retriever import QueryVector


class RemoteEncoder:
    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self._client = httpx.Client(base_url=base_url, timeout=timeout)

    def encode_query(self, text: str) -> QueryVector:
        resp = self._client.post("/encode", json={"text": text})
        resp.raise_for_status()
        data = resp.json()
        return QueryVector(
            dense=data["dense"],
            sparse_indices=data["sparse_indices"],
            sparse_values=data["sparse_values"],
        )

    def close(self) -> None:
        self._client.close()
