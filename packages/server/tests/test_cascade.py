import pytest
from uff_server.reranker import CascadeReranker


class FakeReranker:
    def __init__(self, scores_by_passage: dict[str, float]):
        self.by = scores_by_passage
        self.calls: list[list[str]] = []

    def rerank(self, query: str, passages: list[str]) -> list[float]:
        self.calls.append(list(passages))
        return [self.by.get(p, 0.0) for p in passages]


def test_cascade_uses_colbert_to_preselect_then_cross_for_top():
    passages = ["a", "b", "c", "d", "e"]
    # ColBERT prefere c,b,a; cross-encoder inverte para a>b>c
    colbert = FakeReranker({"a": 0.7, "b": 0.8, "c": 0.9, "d": 0.1, "e": 0.2})
    cross = FakeReranker({"a": 0.99, "b": 0.5, "c": 0.1})
    casc = CascadeReranker(colbert, cross, first_k=3)

    scores = casc.rerank("q", passages)
    order = sorted(range(len(passages)), key=lambda i: scores[i], reverse=True)
    top3 = [passages[i] for i in order[:3]]

    # cross-encoder só vê os 3 melhores do ColBERT (c,b,a)
    assert sorted(cross.calls[0]) == ["a", "b", "c"]
    # ordem final do topo segue o cross-encoder: a > b > c
    assert top3 == ["a", "b", "c"]
    # d,e (fora do first_k) ficam abaixo dos reranqueados
    assert scores[passages.index("a")] > scores[passages.index("d")]


def test_cascade_empty():
    casc = CascadeReranker(FakeReranker({}), FakeReranker({}))
    assert casc.rerank("q", []) == []


class TruncatingReranker:
    """Simula o bug de concorrência do encoder: devolve MENOS scores que passagens."""

    def rerank(self, query: str, passages: list[str]) -> list[float]:
        return [0.5] * (len(passages) - 1)


def test_cascade_falha_claro_se_scores_desalinhados():
    # Encoder sem lock truncava a resposta sob concorrência -> IndexError críptico.
    # Agora o desalinhamento vira RuntimeError com contexto (visível no querylog/journal).
    casc = CascadeReranker(TruncatingReranker(), FakeReranker({}))
    with pytest.raises(RuntimeError, match="colbert.*2 scores.*3 passagens"):
        casc.rerank("q", ["a", "b", "c"])

    casc2 = CascadeReranker(FakeReranker({"a": 0.9, "b": 0.8, "c": 0.7}), TruncatingReranker())
    with pytest.raises(RuntimeError, match="cross-encoder.*2 scores.*3 passagens"):
        casc2.rerank("q", ["a", "b", "c"])
