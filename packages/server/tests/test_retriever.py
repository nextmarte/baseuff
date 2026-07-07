import pytest
from qdrant_client import QdrantClient, models
from uff_server.retriever import DENSE_DIM, QueryVector, retrieve


def _dense(dim: int) -> list[float]:
    v = [0.0] * DENSE_DIM
    v[dim] = 1.0
    return v


class FakeEncoder:
    def __init__(self, qv: QueryVector) -> None:
        self._qv = qv

    def encode_query(self, text: str) -> QueryVector:
        return self._qv


@pytest.fixture
def client():
    c = QdrantClient(location=":memory:")
    c.create_collection(
        "uff",
        vectors_config={
            "dense": models.VectorParams(size=DENSE_DIM, distance=models.Distance.COSINE)
        },
        sparse_vectors_config={"sparse": models.SparseVectorParams()},
    )
    rows = [
        (1, 0, "boletim", "159", "2024-12-27", "licença capacitação do servidor"),
        (2, 1, "boletim", "148", "2024-11-29", "regulamento de férias"),
        (3, 2, "resolucao", "67", "2019-03-13", "conselho universitário decide"),
    ]
    c.upsert(
        "uff",
        points=[
            models.PointStruct(
                id=pid,
                vector={
                    "dense": _dense(dim),
                    "sparse": models.SparseVector(indices=[dim + 10], values=[1.0]),
                },
                payload={
                    "doc_id": pid,
                    "source": src,
                    "numero": num,
                    "publish_date": date,
                    "url": f"http://x/{pid}.pdf",
                    "text": text,
                },
            )
            for pid, dim, src, num, date, text in rows
        ],
    )
    return c


def test_retrieve_ranks_matching_point_first(client):
    enc = FakeEncoder(QueryVector(dense=_dense(0), sparse_indices=[10], sparse_values=[1.0]))
    res = retrieve(client, "uff", enc, "licença", limit=3)
    assert res[0].numero == "159"
    assert res[0].source == "boletim"
    assert "licença" in res[0].text


def test_source_filter_excludes_other_sources(client):
    # encoder aponta para a resolução, mas o filtro restringe a boletim
    enc = FakeEncoder(QueryVector(dense=_dense(2), sparse_indices=[12], sparse_values=[1.0]))
    res = retrieve(client, "uff", enc, "conselho", limit=5, source="boletim")
    assert res, "deve retornar boletins mesmo sem match perfeito"
    assert all(r.source == "boletim" for r in res)
    assert all(r.numero != "67" for r in res)


def test_snippet_collapses_whitespace():
    from uff_server.retriever import SearchResult

    r = SearchResult(0.1, 1, "boletim", "1", None, None, "linha um\n\n   linha dois")
    assert r.snippet == "linha um linha dois"
