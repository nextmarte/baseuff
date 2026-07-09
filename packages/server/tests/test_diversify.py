from uff_server.retriever import SearchResult, _diversify


def _sr(doc_id: int, score: float) -> SearchResult:
    return SearchResult(
        score=score,
        doc_id=doc_id,
        source="boletim",
        numero=str(doc_id),
        publish_date=None,
        url=None,
        text=f"texto {doc_id}",
    )


def test_diversify_caps_chunks_per_document():
    # doc 1 aparece 3x; com teto 2, só 2 entram antes de passar p/ outros docs
    results = [_sr(1, 0.9), _sr(1, 0.8), _sr(1, 0.7), _sr(2, 0.6), _sr(3, 0.5)]
    out = _diversify(results, limit=3, max_per_doc=2)
    assert [r.doc_id for r in out] == [1, 1, 2]  # doc 2 entra no lugar do 3º trecho do doc 1


def test_diversify_backfills_when_few_distinct_docs():
    # só 1 documento disponível: mesmo com teto 1, completa até o limite (não deixa vazio)
    results = [_sr(1, 0.9), _sr(1, 0.8), _sr(1, 0.7)]
    out = _diversify(results, limit=3, max_per_doc=1)
    assert len(out) == 3
    assert [r.doc_id for r in out] == [1, 1, 1]


def test_diversify_preserves_relevance_order():
    results = [_sr(1, 0.9), _sr(2, 0.85), _sr(1, 0.8), _sr(3, 0.7)]
    out = _diversify(results, limit=3, max_per_doc=1)
    assert [r.doc_id for r in out] == [1, 2, 3]  # 1 trecho por doc, na ordem de score
