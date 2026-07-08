"""Índice Qdrant: coleção com vetores nomeados (denso + esparso) e busca híbrida.

Funciona tanto em modo servidor (``url=``) quanto embutido em disco (``path=``),
usado na prova de conceito. A fusão híbrida usa RRF (Reciprocal Rank Fusion). Em
produção o reranking (cross-encoder/ColBERT) fica no servidor MCP (``uff_server``);
este ``hybrid_search`` é o caminho de indexação/POC, sem reranker.
"""

from __future__ import annotations

from dataclasses import dataclass

from qdrant_client import QdrantClient, models

from .embedder import Encoded

DENSE_DIM = 1024


def open_local(path: str) -> QdrantClient:
    return QdrantClient(path=path)


def ensure_collection(client: QdrantClient, name: str) -> None:
    if client.collection_exists(name):
        return
    client.create_collection(
        name,
        vectors_config={
            "dense": models.VectorParams(size=DENSE_DIM, distance=models.Distance.COSINE)
        },
        sparse_vectors_config={"sparse": models.SparseVectorParams()},
    )


def upsert(client: QdrantClient, name: str, point_id: int, enc: Encoded, payload: dict) -> None:
    client.upsert(
        name,
        points=[
            models.PointStruct(
                id=point_id,
                vector={
                    "dense": enc.dense,
                    "sparse": models.SparseVector(
                        indices=enc.sparse_indices, values=enc.sparse_values
                    ),
                },
                payload=payload,
            )
        ],
    )


@dataclass
class Hit:
    score: float
    payload: dict


def _search_leg(client, name, query, using, limit):
    res = client.query_points(name, query=query, using=using, limit=limit, with_payload=True)
    return [(p.id, p.payload) for p in res.points]


def hybrid_search(
    client: QdrantClient, name: str, query: Encoded, *, limit: int = 5, k_rrf: int = 60
) -> list[Hit]:
    """Busca densa + esparsa fundidas por RRF."""
    dense_hits = _search_leg(client, name, query.dense, "dense", limit * 4)
    sparse_hits = _search_leg(
        client,
        name,
        models.SparseVector(indices=query.sparse_indices, values=query.sparse_values),
        "sparse",
        limit * 4,
    )

    scores: dict[int, float] = {}
    payloads: dict[int, dict] = {}
    for ranking in (dense_hits, sparse_hits):
        for rank, (pid, payload) in enumerate(ranking):
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (k_rrf + rank + 1)
            payloads[pid] = payload

    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return [Hit(score=s, payload=payloads[pid]) for pid, s in ordered]
