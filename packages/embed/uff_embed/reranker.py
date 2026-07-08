"""Reranker cross-encoder (BGE-reranker-v2-m3) para afinar o topo da busca.

Recebe (query, passagens) e devolve um score de relevância por passagem. É mais
preciso que a fusão RRF porque o cross-encoder vê query e passagem juntas. Roda no
skynet01 (GPU), ao lado do encoder BGE-M3.
"""

from __future__ import annotations


class Reranker:
    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3", use_fp16: bool = True) -> None:
        from FlagEmbedding import FlagReranker

        self.model = FlagReranker(model_name, use_fp16=use_fp16)

    def scores(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        pairs = [[query, p] for p in passages]
        result = self.model.compute_score(pairs, normalize=True)
        if isinstance(result, (int, float)):
            result = [result]
        return [float(x) for x in result]
