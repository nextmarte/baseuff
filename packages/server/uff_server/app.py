"""Servidor MCP (FastMCP) do BaseUFF — retrieval-only.

Expõe a busca híbrida como tool MCP. O cliente LLM (Claude etc.) chama ``search``
e recebe passagens com citação rastreável (fonte, número, data, URL) para compor
a resposta. O ``QdrantClient`` e o ``QueryEncoder`` são injetados (testável).
"""

from __future__ import annotations

from fastmcp import FastMCP
from qdrant_client import QdrantClient

from .retriever import QueryEncoder, retrieve


def create_app(client: QdrantClient, collection: str, encoder: QueryEncoder) -> FastMCP:
    mcp: FastMCP = FastMCP("BaseUFF")

    @mcp.tool
    def search(query: str, limit: int = 5, source: str | None = None) -> list[dict]:
        """Busca no acervo aberto da UFF (Boletins de Serviço e outros).

        Retorna passagens relevantes com citação (fonte, número, data, URL) para
        que o cliente componha a resposta. ``source`` filtra por fonte
        (ex.: ``"boletim"``); ``limit`` limita o número de passagens.
        """
        results = retrieve(client, collection, encoder, query, limit=limit, source=source)
        return [
            {
                "numero": r.numero,
                "source": r.source,
                "publish_date": r.publish_date,
                "url": r.url,
                "snippet": r.snippet,
                "score": round(r.score, 4),
            }
            for r in results
        ]

    return mcp
