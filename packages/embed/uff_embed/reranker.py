"""Reranker cross-encoder (BGE-reranker-v2-m3) para afinar o topo da busca.

Usa ``sentence_transformers.CrossEncoder``: carrega uma única vez, em processo, numa
GPU — ideal para SERVING (o pool multi-GPU do FlagReranker trava por chamada).
Recebe (query, passagens) e devolve um score de relevância por passagem.
"""

from __future__ import annotations

import math


class Reranker:
    def __init__(
        self, model_name: str = "BAAI/bge-reranker-v2-m3", device: str | None = None
    ) -> None:
        from sentence_transformers import CrossEncoder

        # device=None deixa o ST escolher (cuda se disponível); max_length=512 basta p/ trechos.
        self.model = CrossEncoder(model_name, max_length=512, device=device)

    def scores(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        logits = self.model.predict([[query, p] for p in passages])
        # sigmoid -> 0..1 (a ordem é o que importa; normalizar ajuda a interpretar)
        return [1.0 / (1.0 + math.exp(-float(x))) for x in logits]
