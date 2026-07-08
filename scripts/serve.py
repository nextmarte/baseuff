"""Entrypoint do servidor MCP (ultron).

Liga o FastMCP ao Qdrant local e ao encoder remoto no host GPU:

    uv run python scripts/serve.py                 # stdio (Claude Code/Desktop)
    uv run python scripts/serve.py --http 8000     # HTTP (clientes remotos)
"""

from __future__ import annotations

import argparse

import uvicorn
from qdrant_client import QdrantClient
from uff_core.config import Settings
from uff_server.app import create_app
from uff_server.auth import BearerAuthMiddleware
from uff_server.encoder import RemoteEncoder
from uff_server.reranker import RemoteReranker


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
    reranker = RemoteReranker(settings.encoder_url)  # /rerank no mesmo host GPU
    mcp = create_app(client, settings.qdrant_collection, encoder, reranker=reranker)

    if args.http:
        # App HTTP do MCP protegido por auth Bearer (chaves por agente, em arquivo).
        app = BearerAuthMiddleware(mcp.http_app(), settings.mcp_tokens_path)
        uvicorn.run(app, host=args.host, port=args.http)
    else:
        mcp.run()  # stdio (local, sem auth)


if __name__ == "__main__":
    main()
