"""Recuperação híbrida (denso + esparso, fusão RRF) sobre o Qdrant.

O encoder de query é injetável (``QueryEncoder``): em produção pode ser BGE-M3 em
CPU no ultron ou um endpoint remoto no skynet02; nos testes, um fake. Assim o
servidor MCP fica testável sem torch. A busca aceita filtro por fonte.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from qdrant_client import QdrantClient, models

DENSE_DIM = 1024


@dataclass
class QueryVector:
    dense: list[float]
    sparse_indices: list[int]
    sparse_values: list[float]


class QueryEncoder(Protocol):
    def encode_query(self, text: str) -> QueryVector: ...


@dataclass
class SearchResult:
    score: float
    doc_id: int | None
    source: str | None
    numero: str | None
    publish_date: str | None
    url: str | None
    text: str

    @property
    def snippet(self) -> str:
        return " ".join(self.text.split())[:300]


def _source_filter(source: str | None) -> models.Filter | None:
    if not source:
        return None
    return models.Filter(
        must=[models.FieldCondition(key="source", match=models.MatchValue(value=source))]
    )


def _leg(client, collection, query, using, limit, query_filter):
    return client.query_points(
        collection,
        query=query,
        using=using,
        limit=limit,
        with_payload=True,
        query_filter=query_filter,
    ).points


def retrieve(
    client: QdrantClient,
    collection: str,
    encoder: QueryEncoder,
    query: str,
    *,
    limit: int = 5,
    source: str | None = None,
    k_rrf: int = 60,
) -> list[SearchResult]:
    qv = encoder.encode_query(query)
    query_filter = _source_filter(source)
    dense = _leg(client, collection, qv.dense, "dense", limit * 4, query_filter)
    sparse = _leg(
        client,
        collection,
        models.SparseVector(indices=qv.sparse_indices, values=qv.sparse_values),
        "sparse",
        limit * 4,
        query_filter,
    )

    scores: dict[int, float] = {}
    payloads: dict[int, dict] = {}
    for ranking in (dense, sparse):
        for rank, point in enumerate(ranking):
            scores[point.id] = scores.get(point.id, 0.0) + 1.0 / (k_rrf + rank + 1)
            payloads[point.id] = point.payload or {}

    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    results: list[SearchResult] = []
    for pid, score in ordered:
        p = payloads[pid]
        results.append(
            SearchResult(
                score=score,
                doc_id=p.get("doc_id"),
                source=p.get("source"),
                numero=p.get("numero"),
                publish_date=p.get("publish_date"),
                url=p.get("url"),
                text=p.get("text", ""),
            )
        )
    return results
