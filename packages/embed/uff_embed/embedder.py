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
