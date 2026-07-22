"""Microserviço de encoding de queries (roda no host GPU, ~1.3GB VRAM).

Expõe POST /encode {"text": ...} -> {"dense": [...], "sparse_indices": [...],
"sparse_values": [...]} para o servidor MCP no ultron. Uso:

    uv run uvicorn serve_encoder:app --host 0.0.0.0 --port 8010
"""

from __future__ import annotations

import threading

from fastapi import FastAPI
from pydantic import BaseModel
from uff_embed.embedder import Bge
from uff_embed.reranker import Reranker

app = FastAPI(title="BaseUFF encoder+reranker")
_bge: Bge | None = None
_reranker: Reranker | None = None
# FlagEmbedding NÃO é thread-safe: sob requisições concorrentes (FastAPI roda os
# endpoints `def` em threadpool) as respostas saíam truncadas/misturadas. Um lock
# global serializa a inferência — sem perda de vazão, a GPU já serializa de todo modo.
_gpu_lock = threading.Lock()


def _model() -> Bge:
    global _bge
    if _bge is None:
        _bge = Bge()
    return _bge


def _rr() -> Reranker:
    global _reranker
    if _reranker is None:
        _reranker = Reranker()
    return _reranker


class EncodeRequest(BaseModel):
    text: str


class RerankRequest(BaseModel):
    query: str
    passages: list[str]


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.post("/encode")
def encode(req: EncodeRequest) -> dict:
    with _gpu_lock:
        enc = _model().encode_query(req.text)
    return {
        "dense": enc.dense,
        "sparse_indices": enc.sparse_indices,
        "sparse_values": enc.sparse_values,
    }


@app.post("/rerank")
def rerank(req: RerankRequest) -> dict:
    with _gpu_lock:
        return {"scores": _rr().scores(req.query, req.passages)}


@app.post("/colbert_rerank")
def colbert_rerank(req: RerankRequest) -> dict:
    # late-interaction (MaxSim) usando o próprio BGE-M3 já carregado (sem 2º modelo)
    with _gpu_lock:
        return {"scores": _model().colbert_scores(req.query, req.passages)}
