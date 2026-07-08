"""Entrypoint do servidor MCP (ultron).

Liga o FastMCP ao Qdrant local e ao encoder remoto no host GPU:

    uv run python scripts/serve.py                 # stdio (Claude Code/Desktop)
    uv run python scripts/serve.py --http 8000     # HTTP (clientes remotos)
"""

from __future__ import annotations

import argparse

import uvicorn
from qdrant_client import QdrantClient
from uff_core.catalog import Catalog
from uff_core.config import Settings, sqlite_path
from uff_server.app import build_docs, create_app, render_docs_html
from uff_server.auth import BearerAuthMiddleware
from uff_server.encoder import RemoteEncoder
from uff_server.reranker import CascadeReranker, ColbertReranker, RemoteReranker


def main() -> None:
    ap = argparse.ArgumentParser(description="Servidor MCP BaseUFF")
    ap.add_argument("--http", type=int, default=None, help="porta HTTP (default: stdio)")
    ap.add_argument(
        "--host",
        default="127.0.0.1",
        help="bind do HTTP (default: 127.0.0.1; a exposição pública passa por proxy TLS)",
    )
    args = ap.parse_args()

    settings = Settings()
    client = QdrantClient(url=settings.qdrant_url, timeout=30)
    encoder = RemoteEncoder(settings.encoder_url)
    # Cascata: ColBERT (rápido) pré-seleciona, cross-encoder finaliza o topo.
    # Qualidade do cross-encoder (MRR 1.0) a ~0,65s/consulta (vs ~3s só cross-encoder).
    reranker = CascadeReranker(
        ColbertReranker(settings.encoder_url), RemoteReranker(settings.encoder_url)
    )
    catalog = Catalog(sqlite_path(settings.catalog_dsn))
    collection = settings.qdrant_collection
    mcp = create_app(client, collection, encoder, reranker=reranker, catalog=catalog)

    if args.http:
        # Tools protegidas por auth Bearer; documentação pública em GET /mcp/docs.
        app = BearerAuthMiddleware(
            mcp.http_app(),
            settings.mcp_tokens_path,
            docs_provider=lambda: build_docs(client, collection, catalog),
            html_renderer=render_docs_html,
        )
        uvicorn.run(app, host=args.host, port=args.http)
    else:
        mcp.run()  # stdio (local, sem auth)


if __name__ == "__main__":
    main()
