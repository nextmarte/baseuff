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
        # sbpc: minicurso multi-dia NÃO tem publish_date; mesa-redonda tem o dia
        (4, 3, "sbpc", None, None, "minicurso de astronomia observacional"),
        (5, 4, "sbpc", None, "2026-07-28", "mesa redonda de biologia marinha"),
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
                    "url": f"http://x/{pid}.pdf",
                    "text": text,
                    # como na indexação real: doc sem data não grava a chave
                    **({"publish_date": date} if date else {}),
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


def test_filtro_de_dia_estrito_exclui_docs_sem_data(client):
    enc = FakeEncoder(QueryVector(dense=_dense(3), sparse_indices=[13], sparse_values=[1.0]))
    res = retrieve(
        client,
        "uff",
        enc,
        "minicurso",
        limit=5,
        source="sbpc",
        date_from="2026-07-28",
        date_to="2026-07-28",
    )
    assert all(r.doc_id != 4 for r in res), "sem include_undated, doc sem data fica fora"


def test_include_undated_traz_docs_sem_data_junto_com_o_dia(client):
    # bug real da 78ª RA: minicursos (multi-dia, sem publish_date) sumiam com filtro de dia
    enc = FakeEncoder(QueryVector(dense=_dense(3), sparse_indices=[13], sparse_values=[1.0]))
    res = retrieve(
        client,
        "uff",
        enc,
        "minicurso",
        limit=5,
        source="sbpc",
        date_from="2026-07-28",
        date_to="2026-07-28",
        include_undated=True,
    )
    ids = {r.doc_id for r in res}
    assert 4 in ids, "minicurso sem data deve aparecer"
    assert 5 in ids, "atividade datada do dia também deve aparecer"
    assert all(r.source == "sbpc" for r in res)


def test_snippet_collapses_whitespace():
    from uff_server.retriever import SearchResult

    r = SearchResult(0.1, 1, "boletim", "1", None, None, "linha um\n\n   linha dois")
    assert r.snippet == "linha um linha dois"


def test_limit_maior_que_first_k_nao_vaza_score_sentinela(client):
    # bug real (transcript do agente SBPC): limit=10 com cascata first_k=8 devolvia
    # scores -9/-10 (sentinelas internos do ColBERT) ao cliente
    from uff_server.reranker import CascadeReranker

    class FakePorTexto:
        def __init__(self):
            self.calls = []

        def rerank(self, query, passages):
            self.calls.append(list(passages))
            return [0.5 + 0.01 * i for i in range(len(passages))]

    # mais chunks do MESMO doc: com max_per_doc=1 a diversificação come posições do
    # topo pontuado e, sem a margem/filtro, puxaria a região sentinela p/ fechar o limit
    client.upsert(
        "uff",
        points=[
            models.PointStruct(
                id=100 + i,
                vector={
                    "dense": _dense(0),
                    "sparse": models.SparseVector(indices=[10], values=[1.0]),
                },
                payload={"doc_id": 1, "source": "boletim", "text": f"trecho extra {i}"},
            )
            for i in range(4)
        ],
    )
    colbert, cross = FakePorTexto(), FakePorTexto()
    casc = CascadeReranker(colbert, cross, first_k=2)  # first_k menor que o limit pedido
    enc = FakeEncoder(QueryVector(dense=_dense(0), sparse_indices=[10], sparse_values=[1.0]))
    res = retrieve(client, "uff", enc, "q", limit=5, max_per_doc=1, reranker=casc)
    assert res, "deve retornar resultados"
    assert all(r.score >= 0 for r in res), f"score sentinela vazou: {[r.score for r in res]}"
    ids = [r.doc_id for r in res]
    assert len(ids) == len(set(ids)), "max_per_doc=1 não pode repetir doc"
    # o cross-encoder desta chamada avaliou first_k alargado (>= limit), não só 2
    assert len(cross.calls[0]) >= 5
    # o reranker compartilhado NÃO foi mutado (clone por chamada)
    assert casc.first_k == 2
