"""Cliente do reranker remoto (endpoint /rerank no host GPU)."""

from __future__ import annotations

from typing import Protocol

import httpx


class Reranker(Protocol):
    def rerank(self, query: str, passages: list[str]) -> list[float]: ...


class RemoteReranker:
    """Reranker cross-encoder remoto (endpoint ``/rerank``)."""

    endpoint = "/rerank"

    def __init__(self, base_url: str, timeout: float = 60.0) -> None:
        self._client = httpx.Client(base_url=base_url, timeout=timeout)

    def rerank(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        resp = self._client.post(self.endpoint, json={"query": query, "passages": passages})
        resp.raise_for_status()
        return resp.json()["scores"]

    def close(self) -> None:
        self._client.close()


class ColbertReranker(RemoteReranker):
    """Reranker por late-interaction (ColBERT/MaxSim) remoto (endpoint ``/colbert_rerank``)."""

    endpoint = "/colbert_rerank"


class CascadeReranker:
    """Cascata: ColBERT (barato) pré-seleciona ``first_k``, cross-encoder finaliza esses.
    Junta a latência baixa do ColBERT com a qualidade do cross-encoder no topo.

    Devolve scores alinhados às ``passages``: os ``first_k`` melhores do ColBERT recebem
    o score do cross-encoder (com offset para ficarem acima); o resto mantém a ordem do
    ColBERT, abaixo. O retriever ordena por score e corta em ``limit`` (<= ``first_k``)."""

    def __init__(self, colbert: Reranker, cross: Reranker, first_k: int = 8) -> None:
        self.colbert = colbert
        self.cross = cross
        self.first_k = first_k

    def rerank(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        col = self.colbert.rerank(query, passages)
        order = sorted(range(len(passages)), key=lambda i: col[i], reverse=True)
        top = order[: self.first_k]
        cross = self.cross.rerank(query, [passages[i] for i in top])
        # Os finalizados pelo cross-encoder recebem seu score REAL (0..1, interpretável e
        # logável); os demais ficam negativos (abaixo), mantendo a ordem do ColBERT. Como
        # limit <= first_k, o cliente só vê os scores reais do cross-encoder.
        scores = [0.0] * len(passages)
        for i, idx in enumerate(order):
            scores[idx] = -1.0 - i
        for j, idx in enumerate(top):
            scores[idx] = float(cross[j])
        return scores
