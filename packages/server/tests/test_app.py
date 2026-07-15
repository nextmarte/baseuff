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
    v2 = [0.0] * DENSE_DIM
    v2[0] = 0.8
    v2[1] = 0.6
    c.upsert(
        "uff",
        points=[
            models.PointStruct(
                id=1,
                vector={"dense": v, "sparse": models.SparseVector(indices=[10], values=[1.0])},
                payload={
                    # payload ANTIGO (sem title/tipo/extra): search não pode quebrar
                    "doc_id": 1,
                    "source": "boletim",
                    "numero": "159",
                    "publish_date": "2024-12-27",
                    "url": "http://x/1.pdf",
                    "text": "licença capacitação do servidor",
                },
            ),
            models.PointStruct(
                id=20000,
                # vetor um pouco menos alinhado à query fake: sem filtro, o boletim vem 1º
                vector={"dense": v2, "sparse": models.SparseVector(indices=[11], values=[1.0])},
                payload={
                    "doc_id": 2,
                    "source": "sbpc",
                    "numero": None,
                    "publish_date": "2026-07-29",
                    "url": "https://reunioes2.sbpcnet.org.br/programacao/#2026-07-29-cotas",
                    "title": "COTAS EM DISPUTA",
                    "orgao": "78ª RA — Mesa-Redonda",
                    "tipo": "mesa-redonda",
                    "extra": {
                        "tipo": "mesa-redonda",
                        "horario": "13h00 às 15h30",
                        "modalidade": "Presencial",
                        "local": "Campus Gragoatá — Bloco A",
                        "coordenador": "Ana Paula da Silva (UFF)",
                        "palestrantes": ["Danieli Balbi (ALERJ)"],
                        "trilha": None,
                    },
                    "text": (
                        "Mesa-Redonda: COTAS EM DISPUTA. Palestrantes: Danieli Balbi (ALERJ), "
                        "inscrição com CPF 123.456.789-09 na 78ª Reunião Anual da SBPC."
                    ),
                },
            ),
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
    assert natureza("sbpc") == "evento"
    assert natureza(None) == "documento"  # default seguro


async def test_search_tool_is_registered():
    app = create_app(_seeded_client(), "uff", FakeEncoder())
    tool = await app.get_tool("search")
    assert tool is not None and tool.name == "search"


async def test_sbpc_tool_is_registered():
    app = create_app(_seeded_client(), "uff", FakeEncoder())
    tool = await app.get_tool("sbpc")
    assert tool is not None and tool.name == "sbpc"


async def test_sbpc_end_to_end_campos_estruturados_e_mask_cpf():
    app = create_app(_seeded_client(), "uff", FakeEncoder())
    async with Client(app) as client:
        result = await client.call_tool("sbpc", {"query": "cotas"})
    data = result.data
    assert isinstance(data, list) and len(data) == 1  # só o doc sbpc (source fixa)
    r = data[0]
    assert r["titulo"] == "COTAS EM DISPUTA"
    assert r["tipo"] == "mesa-redonda"
    assert r["dia"] == "2026-07-29"
    assert r["horario"] == "13h00 às 15h30"
    assert r["local"] == "Campus Gragoatá — Bloco A"
    assert r["coordenador"] == "Ana Paula da Silva (UFF)"
    assert r["palestrantes"] == ["Danieli Balbi (ALERJ)"]
    assert r["natureza"] == "evento"
    assert "123.456.789-09" not in r["snippet"]  # CPF mascarado na saída


async def test_sbpc_filtro_dia_e_normalizacao():
    app = create_app(_seeded_client(), "uff", FakeEncoder())
    async with Client(app) as client:
        no_dia = (await client.call_tool("sbpc", {"query": "cotas", "dia": "2026-07-29"})).data
        outro_dia = (await client.call_tool("sbpc", {"query": "cotas", "dia": "2026-07-30"})).data
        ddmm = (await client.call_tool("sbpc", {"query": "cotas", "dia": "29/07"})).data
    assert len(no_dia) == 1
    assert outro_dia == []
    assert len(ddmm) == 1  # "29/07" normalizado p/ 2026-07-29


async def test_sbpc_filtro_tipo():
    app = create_app(_seeded_client(), "uff", FakeEncoder())
    async with Client(app) as client:
        mesa = (await client.call_tool("sbpc", {"query": "cotas", "tipo": "Mesa Redonda"})).data
        conf = (await client.call_tool("sbpc", {"query": "cotas", "tipo": "conferencia"})).data
    assert len(mesa) == 1  # "Mesa Redonda" normalizado p/ mesa-redonda
    assert conf == []


async def test_search_com_source_sbpc_e_payload_antigo_nao_quebra():
    app = create_app(_seeded_client(), "uff", FakeEncoder())
    async with Client(app) as client:
        sbpc = (await client.call_tool("search", {"query": "cotas", "source": "sbpc"})).data
        antigo = (await client.call_tool("search", {"query": "licença", "source": "boletim"})).data
    assert len(sbpc) == 1 and sbpc[0]["natureza"] == "evento"
    assert len(antigo) == 1  # payload sem title/tipo/extra continua funcionando


async def test_dia_iso_e_tipo_slug():
    from uff_server.app import _dia_iso, _tipo_slug

    assert _dia_iso("2026-07-29") == "2026-07-29"
    assert _dia_iso("29/07") == "2026-07-29"
    assert _dia_iso("1/8/2026") == "2026-08-01"
    assert _dia_iso(None) is None
    assert _tipo_slug("Mesa Redonda") == "mesa-redonda"
    assert _tipo_slug("Conferência") == "conferencia"
    assert _tipo_slug(None) is None
