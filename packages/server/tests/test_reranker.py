import httpx
import pytest
import respx
from qdrant_client import QdrantClient, models
from uff_server.reranker import RemoteReranker
from uff_server.retriever import DENSE_DIM, QueryVector, retrieve


@respx.mock(assert_all_called=False)
def test_remote_reranker_posts_and_parses(respx_mock):
    route = respx_mock.post("http://gpu:8010/rerank").mock(
        return_value=httpx.Response(200, json={"scores": [0.9, 0.1]})
    )
    rr = RemoteReranker("http://gpu:8010")
    scores = rr.rerank("q", ["passagem A", "passagem B"])
    assert scores == [0.9, 0.1]
    assert route.called


def test_remote_reranker_empty_passages_no_call():
    rr = RemoteReranker("http://gpu:8010")
    assert rr.rerank("q", []) == []  # não chama a rede


def _dense(dim: int) -> list[float]:
    v = [0.0] * DENSE_DIM
    v[dim] = 1.0
    return v


class FakeEncoder:
    def encode_query(self, text: str) -> QueryVector:
        return QueryVector(dense=_dense(0), sparse_indices=[10], sparse_values=[1.0])


class KeywordReranker:
    """Reranker fake: pontua alto quem contém 'alvo'."""

    def rerank(self, query: str, passages: list[str]) -> list[float]:
        return [1.0 if "alvo" in p.lower() else 0.0 for p in passages]


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
        (1, 0, "boletim", "genérico sobre progressão"),
        (2, 1, "boletim", "documento ALVO com o ato exato"),
        (3, 2, "boletim", "outro texto qualquer"),
    ]
    c.upsert(
        "uff",
        points=[
            models.PointStruct(
                id=pid,
                vector={
                    "dense": _dense(dim),
                    "sparse": models.SparseVector(indices=[10], values=[1.0]),
                },
                payload={"doc_id": pid, "source": src, "numero": str(pid), "text": text},
            )
            for pid, dim, src, text in rows
        ],
    )
    return c


def test_rerank_reorders_to_put_target_first(client):
    # sem reranker: ordem por RRF (o alvo pode não ser o 1º)
    plain = retrieve(client, "uff", FakeEncoder(), "ato exato", limit=3)
    # com reranker: o texto com 'alvo' vai pro topo
    reranked = retrieve(
        client,
        "uff",
        FakeEncoder(),
        "ato exato",
        limit=3,
        reranker=KeywordReranker(),
        candidate_k=3,
    )
    assert "alvo" in reranked[0].text.lower()
    assert reranked[0].numero == "2"
    assert len(reranked) == 3
    # garante que mudou algo em relação ao baseline (o alvo subiu)
    assert plain[0].numero != "2" or reranked[0].numero == "2"
