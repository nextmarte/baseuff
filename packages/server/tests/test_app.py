import pytest
from fastmcp import Client
from qdrant_client import QdrantClient, models
from uff_server.app import create_app
from uff_server.retriever import DENSE_DIM, QueryVector

pytestmark = pytest.mark.asyncio


class FakeEncoder:
    def encode_query(self, text: str) -> QueryVector:
        v = [0.0] * DENSE_DIM
        v[0] = 1.0
        return QueryVector(dense=v, sparse_indices=[10], sparse_values=[1.0])


def _seeded_client() -> QdrantClient:
    c = QdrantClient(location=":memory:")
    c.create_collection(
        "uff",
        vectors_config={
            "dense": models.VectorParams(size=DENSE_DIM, distance=models.Distance.COSINE)
        },
        sparse_vectors_config={"sparse": models.SparseVectorParams()},
    )
    v = [0.0] * DENSE_DIM
    v[0] = 1.0
    c.upsert(
        "uff",
        points=[
            models.PointStruct(
                id=1,
                vector={"dense": v, "sparse": models.SparseVector(indices=[10], values=[1.0])},
                payload={
                    "doc_id": 1,
                    "source": "boletim",
                    "numero": "159",
                    "publish_date": "2024-12-27",
                    "url": "http://x/1.pdf",
                    "text": "licença capacitação do servidor",
                },
            )
        ],
    )
    return c


async def test_search_tool_end_to_end():
    app = create_app(_seeded_client(), "uff", FakeEncoder())
    async with Client(app) as client:
        result = await client.call_tool("search", {"query": "licença", "limit": 3})
    data = result.data
    assert isinstance(data, list) and data
    assert data[0]["numero"] == "159"
    assert data[0]["url"].endswith("1.pdf")
    assert "licença" in data[0]["snippet"]
    assert data[0]["natureza"] == "documento"  # boletim é ato oficial, não tutorial


async def test_natureza_classifica_tutorial_vs_documento():
    from uff_server.app import natureza

    assert natureza("sti_kb") == "tutorial"
    assert natureza("boletim") == "documento"
    assert natureza("pesquisa") == "documento"
    assert natureza(None) == "documento"  # default seguro


async def test_search_tool_is_registered():
    app = create_app(_seeded_client(), "uff", FakeEncoder())
    tool = await app.get_tool("search")
    assert tool is not None and tool.name == "search"
