"""Recuperação híbrida (denso + esparso, fusão RRF) sobre o Qdrant.

O encoder de query é injetável (``QueryEncoder``): em produção pode ser BGE-M3 em
CPU no ultron ou um endpoint remoto no skynet02; nos testes, um fake. Assim o
servidor MCP fica testável sem torch. A busca aceita filtro por fonte.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
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
    tipo: str | None = None,
    include_undated: bool = False,
) -> models.Filter | None:
    """Filtro Qdrant combinando fonte (keyword), período (datetime), termo (full-text)
    e tipo de conteúdo (keyword; ex.: 'mesa-redonda' na fonte sbpc).

    ``include_undated``: docs SEM ``publish_date`` passam pelo filtro de período (OR).
    Na fonte sbpc, minicursos/pôsteres/serviço são multi-dia e não têm data — um
    filtro de dia estrito os esconderia (era a causa dos zero-resultados da tool)."""
    must: list = []
    if source:
        must.append(models.FieldCondition(key="source", match=models.MatchValue(value=source)))
    if tipo:
        must.append(models.FieldCondition(key="tipo", match=models.MatchValue(value=tipo)))
    if date_from or date_to:
        no_periodo = models.FieldCondition(
            key="publish_date", range=models.DatetimeRange(gte=date_from, lte=date_to)
        )
        if include_undated:
            sem_data = models.IsEmptyCondition(is_empty=models.PayloadField(key="publish_date"))
            must.append(models.Filter(should=[no_periodo, sem_data]))
        else:
            must.append(no_periodo)
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
    title: str | None = None
    extra: dict = field(default_factory=dict)

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
        title=payload.get("title"),
        extra=payload.get("extra") or {},
    )


def _diversify(results: list[SearchResult], limit: int, max_per_doc: int) -> list[SearchResult]:
    """Limita quantos trechos do MESMO documento entram no top-k (mais documentos
    distintos), preservando a ordem; se faltar, completa com os que excederam o teto."""
    counts: dict = {}
    primary: list[SearchResult] = []
    overflow: list[SearchResult] = []
    for r in results:
        n = counts.get(r.doc_id, 0)
        if r.doc_id is not None and n < max_per_doc:
            primary.append(r)
            counts[r.doc_id] = n + 1
        else:
            overflow.append(r)
    return (primary + overflow)[:limit]


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
    tipo: str | None = None,
    include_undated: bool = False,
    k_rrf: int = 60,
    reranker=None,
    candidate_k: int | None = None,
    max_per_doc: int = 2,
) -> list[SearchResult]:
    """Busca híbrida (denso+esparso, RRF) com filtros opcionais de fonte, período e tipo.
    Se ``reranker`` for dado, faz over-fetch de ``candidate_k`` candidatos e reordena
    pelo cross-encoder. ``max_per_doc`` diversifica: no máx. N trechos por documento.
    ``include_undated``: ver :func:`_build_filter` (docs sem data passam pelo período)."""
    fetch = candidate_k if (reranker is not None and candidate_k) else max(limit * 4, 24)

    qv = encoder.encode_query(query)
    query_filter = _build_filter(
        source=source,
        date_from=date_from,
        date_to=date_to,
        tipo=tipo,
        include_undated=include_undated,
    )
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
        return _diversify(candidates, limit, max_per_doc)

    # Cascata com first_k < limit deixaria os scores-sentinela (negativos) das posições
    # além do topo vazarem para o cliente. Alarga o first_k desta chamada (clone; o
    # reranker compartilhado não pode ser mutado sob concorrência) com folga de 2×:
    # a diversificação (max_per_doc) descarta trechos do topo pontuado e, sem margem,
    # puxaria candidatos da região sentinela para completar o limit.
    first_k = getattr(reranker, "first_k", None)
    if first_k is not None and limit > first_k:
        alvo = min(len(candidates), limit * 2)
        reranker = type(reranker)(reranker.colbert, reranker.cross, first_k=alvo)

    rerank_scores = reranker.rerank(query, [c.text for c in candidates])
    reranked = sorted(
        zip(candidates, rerank_scores, strict=False), key=lambda cs: cs[1], reverse=True
    )
    ranked: list[SearchResult] = []
    for cand, rs in reranked:
        if first_k is not None and rs < 0:
            continue  # região sentinela da cascata: nunca chega ao cliente
        cand.score = float(rs)
        ranked.append(cand)
    return _diversify(ranked, limit, max_per_doc)


def _name_gap_pattern(nome: str, max_gap: int = 3):
    """Regex: tokens do nome EM ORDEM com até ``max_gap`` palavras entre eles (pega nomes
    compostos, ex.: 'Mariana Marinho Peixoto' casa 'Mariana Marinho da Costa Lima Peixoto')."""
    toks = [re.escape(t) for t in _fold(nome).split() if len(t) >= 2]
    if not toks:
        return None
    joiner = rf"(?:\s+\w+){{0,{max_gap}}}\s+"
    return re.compile(r"\b" + joiner.join(toks) + r"\b")


def _dossier_entry(pay: dict, nome: str) -> dict:
    return {
        "numero": pay.get("numero"),
        "source": pay.get("source"),
        "publish_date": pay.get("publish_date"),
        "url": pay.get("url"),
        "snippet": snippet_around(pay.get("text", ""), nome),
    }


def dossier(
    client: QdrantClient,
    collection: str,
    nome: str,
    *,
    source: str | None = "boletim",
    max_scan: int = 6000,
) -> dict:
    """Levantamento EXAUSTIVO por pessoa/entidade (não top-k). Varre todo o acervo (full-text
    MatchText) e classifica cada documento em dois níveis, deduplicado por doc e cronológico:

    - ``confirmados``: nome CONTÍGUO no texto (alta precisão — é a pessoa).
    - ``provaveis``: mesmos tokens em ORDEM com partes no meio (recupera nomes compostos, ex.:
      'Mariana Marinho Peixoto' → 'Mariana Marinho da Costa Lima Peixoto'). Podem incluir
      homônimos com sobrenomes intermediários — o cliente deve VERIFICAR.
    """
    alvo = _fold(nome)
    pattern = _name_gap_pattern(nome)
    query_filter = _build_filter(source=source, text_match=nome)
    conf: dict[tuple, dict] = {}
    prov: dict[tuple, dict] = {}
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
            folded = _fold(pay.get("text", ""))
            key = (pay.get("source"), pay.get("numero"), pay.get("publish_date"))
            if alvo in folded:
                conf.setdefault(key, _dossier_entry(pay, nome))
            elif pattern is not None and pattern.search(folded):
                prov.setdefault(key, _dossier_entry(pay, nome))
        if offset is None:
            break
    # um documento confirmado não deve reaparecer entre os prováveis
    for key in conf:
        prov.pop(key, None)
    by_date = lambda e: e.get("publish_date") or ""  # noqa: E731
    return {
        "confirmados": sorted(conf.values(), key=by_date),
        "provaveis": sorted(prov.values(), key=by_date),
    }


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
