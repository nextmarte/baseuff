"""Microserviço de encoding de queries (roda no host GPU, ~1.3GB VRAM).

Expõe POST /encode {"text": ...} -> {"dense": [...], "sparse_indices": [...],
"sparse_values": [...]} para o servidor MCP no ultron. Uso:

    uv run uvicorn serve_encoder:app --host 0.0.0.0 --port 8010
"""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel
from uff_embed.embedder import Bge
from uff_embed.reranker import Reranker

app = FastAPI(title="BaseUFF encoder+reranker")
_bge: Bge | None = None
_reranker: Reranker | None = None


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
    enc = _model().encode_query(req.text)
    return {
        "dense": enc.dense,
        "sparse_indices": enc.sparse_indices,
        "sparse_values": enc.sparse_values,
    }


@app.post("/rerank")
def rerank(req: RerankRequest) -> dict:
    return {"scores": _rr().scores(req.query, req.passages)}
