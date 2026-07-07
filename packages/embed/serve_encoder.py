"""Microserviço de encoding de queries (roda no host GPU, ~1.3GB VRAM).

Expõe POST /encode {"text": ...} -> {"dense": [...], "sparse_indices": [...],
"sparse_values": [...]} para o servidor MCP no ultron. Uso:

    uv run uvicorn serve_encoder:app --host 0.0.0.0 --port 8010
"""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel
from uff_embed.embedder import Bge

app = FastAPI(title="BaseUFF encoder")
_bge: Bge | None = None


def _model() -> Bge:
    global _bge
    if _bge is None:
        _bge = Bge()
    return _bge


class EncodeRequest(BaseModel):
    text: str


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
