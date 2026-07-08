"""Embeddings BGE-M3 (denso + esparso) para busca híbrida.

Um único modelo gera o vetor denso (1024-d) e os pesos lexicais esparsos, que
alimentam as duas pernas da busca híbrida no Qdrant. Roda no skynet02 (GPU).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Encoded:
    dense: list[float]
    sparse_indices: list[int]
    sparse_values: list[float]


class Bge:
    def __init__(self, model_name: str = "BAAI/bge-m3", use_fp16: bool = True) -> None:
        from FlagEmbedding import BGEM3FlagModel

        self.model = BGEM3FlagModel(model_name, use_fp16=use_fp16)

    def encode(
        self, texts: list[str], batch_size: int = 8, max_length: int = 1024
    ) -> list[Encoded]:
        out = self.model.encode(
            texts,
            batch_size=batch_size,
            max_length=max_length,
            return_dense=True,
            return_sparse=True,
            return_colbert_vecs=False,
        )
        dense = out["dense_vecs"]
        lexical = out["lexical_weights"]
        encoded: list[Encoded] = []
        for i in range(len(texts)):
            weights = lexical[i]
            encoded.append(
                Encoded(
                    dense=[float(x) for x in dense[i]],
                    sparse_indices=[int(k) for k in weights.keys()],
                    sparse_values=[float(v) for v in weights.values()],
                )
            )
        return encoded

    def encode_query(self, text: str, max_length: int = 512) -> Encoded:
        return self.encode([text], batch_size=1, max_length=max_length)[0]

    def colbert_scores(
        self, query: str, passages: list[str], max_length: int = 512
    ) -> list[float]:
        """Reranking por late-interaction (ColBERT): MaxSim entre os vetores-token da
        query e de cada passagem. Nativo do BGE-M3 (return_colbert_vecs); tudo na GPU.
        Score = média_i(max_j(q_i · p_j)) — normalizado pelo nº de tokens da query."""
        if not passages:
            return []
        import numpy as np

        out = self.model.encode(
            [query, *passages],
            batch_size=min(len(passages) + 1, 32),
            max_length=max_length,
            return_dense=False,
            return_sparse=False,
            return_colbert_vecs=True,
        )
        vecs = out["colbert_vecs"]
        q = np.asarray(vecs[0], dtype=np.float32)  # (mq, dim)
        scores: list[float] = []
        for p in vecs[1:]:
            p = np.asarray(p, dtype=np.float32)  # (np, dim)
            sim = q @ p.T  # (mq, np)
            scores.append(float(sim.max(axis=1).mean()))  # MaxSim médio por token de query
        return scores
