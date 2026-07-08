"""Configuração central do BaseUFF (variáveis de ambiente com prefixo ``UFF_``).

Lê de ``.env`` (quando presente) e do ambiente. Ver ``.env.example`` para a lista
completa e os valores de referência.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="UFF_", env_file=".env", extra="ignore")

    # Qdrant (serving)
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "uff_chunks"

    # Catálogo
    catalog_dsn: str = "sqlite:///data/catalog.db"

    # Armazenamento local do acervo
    data_dir: str = "data"

    # Crawler
    user_agent: str = "BaseUFF-crawler/0.1 (+contato: contato.papagaionextech@gmail.com)"
    requests_per_second: float = 1.0
    max_concurrency: int = 4
    boletim_start_year: int = 2010

    # Host de GPU: skynet01 (embed em batch + microserviço de encode/rerank online).
    # skynet02 fica livre para outros serviços do usuário. Acesso por chave SSH (sem senha).
    embed_host: str = "cid-uff.net"
    embed_ssh_port: int = 22023  # skynet01 (10.171.69.10)
    embed_ssh_user: str = "marcus"
    embed_model: str = "BAAI/bge-m3"

    # Microserviço no skynet01: /encode (BGE-M3) + /rerank (cross-encoder) + /colbert_rerank
    encoder_url: str = "http://10.171.69.10:8010"

    # Auth do servidor MCP: arquivo de tokens (agente<espaço>token por linha)
    mcp_tokens_path: str = "data/mcp_tokens.txt"


def sqlite_path(dsn: str) -> str:
    """Extrai o caminho de arquivo de um DSN ``sqlite:///...``.

    ``sqlite:///data/catalog.db`` -> ``data/catalog.db`` (relativo)
    ``sqlite:////abs/catalog.db``  -> ``/abs/catalog.db`` (absoluto)
    """
    prefix = "sqlite:///"
    if not dsn.startswith(prefix):
        raise ValueError(f"DSN não é SQLite: {dsn!r}")
    return dsn[len(prefix) :]
