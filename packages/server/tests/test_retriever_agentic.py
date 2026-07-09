from types import SimpleNamespace

from uff_server.retriever import (
    _build_filter,
    _fold,
    dossier,
    snippet_around,
)


# ---------- helpers puros ----------
def test_fold_strips_accents_and_preserves_length():
    assert _fold("Aurélio") == "aurelio"
    assert len(_fold("Aurélio")) == len("Aurélio")  # posições preservadas p/ snippet
    assert _fold("LICENÇA") == "licenca"


def test_snippet_around_centers_on_term():
    text = "x" * 400 + " PORTARIA de progressão do servidor " + "y" * 400
    snip = snippet_around(text, "progressão servidor", width=120)
    assert "progress" in _fold(snip)
    assert len(snip) <= 130
    assert snip.startswith("…")  # cortou antes


def test_snippet_around_falls_back_to_start():
    text = "conteúdo curto sem o termo procurado"
    snip = snippet_around(text, "kubernetes", width=100)
    assert snip.startswith("conteúdo curto")


def test_build_filter_combines_source_and_dates():
    f = _build_filter(source="boletim", date_from="2020-01-01", date_to="2020-12-31")
    keys = [c.key for c in f.must]
    assert "source" in keys and "publish_date" in keys


def test_build_filter_none_when_empty():
    assert _build_filter() is None


# ---------- dossiê ----------
# O full-text (MatchText) não roda no Qdrant local; testamos a lógica de pós-filtro,
# dedup e ordenação com um client fake de scroll (a integração real é validada no servidor).
class FakeScrollClient:
    def __init__(self, rows):
        self._pts = [
            SimpleNamespace(
                payload={
                    "source": "boletim",
                    "numero": num,
                    "publish_date": d,
                    "url": f"u/{num}",
                    "text": t,
                }
            )
            for num, d, t in rows
        ]

    def scroll(self, collection, scroll_filter=None, limit=256, offset=None, with_payload=None):
        return self._pts, None  # ignora o filtro; o pós-filtro por substring é que decide


def test_dossier_confirmados_deduped_and_chronological():
    rows = [
        ("010", "2020-01-05", "Designar JOAO DA SILVA para a comissão."),
        ("020", "2020-02-10", "Progressão de JOAO DA SILVA, matrícula 123."),
        ("020", "2020-02-10", "Outra parte do mesmo boletim 020 sobre JOAO DA SILVA."),  # dup
        ("030", "2019-03-01", "Aposentadoria de JOAO DA SILVA."),  # mais antigo
        ("040", "2020-05-01", "Nomear JOAO PEREIRA e MARIA DA SILVA."),  # NÃO é ele
    ]
    r = dossier(FakeScrollClient(rows), "uff", "Joao da Silva", source="boletim")
    numeros = [e["numero"] for e in r["confirmados"]]
    assert numeros == ["030", "010", "020"]  # cronológico, sem duplicar 020, sem o 040
    assert all("silva" in _fold(e["snippet"]) for e in r["confirmados"])


def test_dossier_compound_middle_name_goes_to_provaveis():
    # nome buscado é subsequência do nome completo (partes no meio) -> "provaveis", não "confirmados"
    rows = [
        ("100", "2021-01-01", "Designar MARIANA MARINHO DA COSTA LIMA PEIXOTO, SIAPE 123."),
        ("101", "2021-02-01", "Nomear MARIANA MARINHO PEIXOTO como coordenadora."),  # contíguo
    ]
    r = dossier(FakeScrollClient(rows), "uff", "Mariana Marinho Peixoto", source="boletim")
    assert [e["numero"] for e in r["confirmados"]] == ["101"]  # contíguo
    assert [e["numero"] for e in r["provaveis"]] == ["100"]  # composto (a verificar)
