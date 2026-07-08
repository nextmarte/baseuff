"""Recuperação híbrida (denso + esparso, fusão RRF) sobre o Qdrant.

O encoder de query é injetável (``QueryEncoder``): em produção pode ser BGE-M3 em
CPU no ultron ou um endpoint remoto no skynet02; nos testes, um fake. Assim o
servidor MCP fica testável sem torch. A busca aceita filtro por fonte.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Protocol

from qdrant_client import QdrantClient, models

DENSE_DIM = 1024


def _fold(s: str | None) -> str:
    """Minúsculas + remove acentos PRESERVANDO o comprimento (posições mapeiam 1:1
    para o texto original — essencial para recortar snippet e casar substring)."""
    out = []
    for ch in s or "":
        base = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c))
        out.append((base or ch)[:1].lower())
    return "".join(out)


def snippet_around(text: str, query: str, width: int = 300) -> str:
    """Recorta uma janela do texto centrada na 1ª ocorrência de um termo da query."""
    clean = " ".join((text or "").split())
    folded = _fold(clean)
    fq = _fold(query).strip()
    idx = folded.find(fq)  # tenta a frase inteira primeiro (ex.: nome completo no dossiê)
    if idx < 0:
        terms = [t for t in fq.split() if len(t) >= 3]
        idx = min((folded.find(t) for t in terms if folded.find(t) >= 0), default=-1)
    if idx < 0:
        return clean[:width]
    start = max(0, idx - width // 3)
    end = min(len(clean), start + width)
    return ("…" if start > 0 else "") + clean[start:end] + ("…" if end < len(clean) else "")


def _build_filter(
    source: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    text_match: str | None = None,
) -> models.Filter | None:
    """Filtro Qdrant combinando fonte (keyword), período (datetime) e termo (full-text)."""
    must: list[models.FieldCondition] = []
    if source:
        must.append(models.FieldCondition(key="source", match=models.MatchValue(value=source)))
    if date_from or date_to:
        must.append(
            models.FieldCondition(
                key="publish_date", range=models.DatetimeRange(gte=date_from, lte=date_to)
            )
        )
    if text_match:
        must.append(models.FieldCondition(key="text", match=models.MatchText(text=text_match)))
    return models.Filter(must=must) if must else None


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


def _result(score: float, payload: dict) -> SearchResult:
    return SearchResult(
        score=score,
        doc_id=payload.get("doc_id"),
        source=payload.get("source"),
        numero=payload.get("numero"),
        publish_date=payload.get("publish_date"),
        url=payload.get("url"),
        text=payload.get("text", ""),
    )


def retrieve(
    client: QdrantClient,
    collection: str,
    encoder: QueryEncoder,
    query: str,
    *,
    limit: int = 5,
    source: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    k_rrf: int = 60,
    reranker=None,
    candidate_k: int | None = None,
) -> list[SearchResult]:
    """Busca híbrida (denso+esparso, RRF) com filtros opcionais de fonte e período.
    Se ``reranker`` for dado, faz over-fetch de ``candidate_k`` candidatos e reordena
    pelo cross-encoder (mais preciso no topo)."""
    fetch = candidate_k if (reranker is not None and candidate_k) else limit
    if reranker is not None and not candidate_k:
        # over-fetch enxuto: reranquear ~24 candidatos mantém a qualidade e corta a
        # latência (o cross-encoder custa ~40ms/par; 80 candidatos = ~3s).
        fetch = max(limit * 4, 24)

    qv = encoder.encode_query(query)
    query_filter = _build_filter(source=source, date_from=date_from, date_to=date_to)
    dense = _leg(client, collection, qv.dense, "dense", fetch * 4, query_filter)
    sparse = _leg(
        client,
        collection,
        models.SparseVector(indices=qv.sparse_indices, values=qv.sparse_values),
        "sparse",
        fetch * 4,
        query_filter,
    )

    scores: dict[int, float] = {}
    payloads: dict[int, dict] = {}
    for ranking in (dense, sparse):
        for rank, point in enumerate(ranking):
            scores[point.id] = scores.get(point.id, 0.0) + 1.0 / (k_rrf + rank + 1)
            payloads[point.id] = point.payload or {}

    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:fetch]
    candidates = [_result(sc, payloads[pid]) for pid, sc in ordered]

    if reranker is None:
        return candidates[:limit]

    rerank_scores = reranker.rerank(query, [c.text for c in candidates])
    reranked = sorted(
        zip(candidates, rerank_scores, strict=False), key=lambda cs: cs[1], reverse=True
    )
    top: list[SearchResult] = []
    for cand, rs in reranked[:limit]:
        cand.score = float(rs)
        top.append(cand)
    return top


def dossier(
    client: QdrantClient,
    collection: str,
    nome: str,
    *,
    source: str | None = "boletim",
    max_scan: int = 4000,
) -> list[dict]:
    """Levantamento EXAUSTIVO por pessoa/entidade (não top-k): varre todo o acervo com
    full-text (MatchText), pós-filtra pela ocorrência do nome contíguo (precisão),
    deduplica por documento e ordena cronologicamente. Fecha a limitação do top-k."""
    alvo = _fold(nome)
    query_filter = _build_filter(source=source, text_match=nome)
    seen: dict[tuple, dict] = {}
    offset = None
    scanned = 0
    while scanned < max_scan:
        points, offset = client.scroll(
            collection,
            scroll_filter=query_filter,
            limit=256,
            offset=offset,
            with_payload=["numero", "publish_date", "url", "text", "source"],
        )
        for p in points:
            scanned += 1
            pay = p.payload or {}
            if alvo in _fold(pay.get("text", "")):  # pós-filtro: nome contíguo
                key = (pay.get("source"), pay.get("numero"), pay.get("publish_date"))
                seen.setdefault(
                    key,
                    {
                        "numero": pay.get("numero"),
                        "source": pay.get("source"),
                        "publish_date": pay.get("publish_date"),
                        "url": pay.get("url"),
                        "snippet": snippet_around(pay.get("text", ""), nome),
                    },
                )
        if offset is None:
            break
    return sorted(seen.values(), key=lambda e: e.get("publish_date") or "")


def get_document(
    client: QdrantClient,
    collection: str,
    *,
    doc_id: int | None = None,
    numero: str | None = None,
    source: str | None = "boletim",
    max_chunks: int = 800,
) -> dict | None:
    """Reconstrói um documento inteiro (todos os chunks, na ordem) para dar contexto
    pleno ao agente. Preferir ``doc_id`` (único); ``numero`` pode ser ambíguo entre anos."""
    must: list[models.FieldCondition] = []
    if doc_id is not None:
        must.append(models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id)))
    else:
        if source:
            must.append(models.FieldCondition(key="source", match=models.MatchValue(value=source)))
        if numero:
            must.append(models.FieldCondition(key="numero", match=models.MatchValue(value=numero)))
    query_filter = models.Filter(must=must) if must else None
    points, _ = client.scroll(
        collection, scroll_filter=query_filter, limit=max_chunks, with_payload=True
    )
    if not points:
        return None
    chunks = sorted(points, key=lambda p: (p.payload or {}).get("chunk_index") or 0)
    head = chunks[0].payload or {}
    return {
        "doc_id": head.get("doc_id"),
        "source": head.get("source"),
        "numero": head.get("numero"),
        "publish_date": head.get("publish_date"),
        "url": head.get("url"),
        "n_chunks": len(chunks),
        "texto": "\n\n".join((c.payload or {}).get("text", "") for c in chunks),
    }
