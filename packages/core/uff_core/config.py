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

    # Host de vetorização (skynet02) — sem senha; acesso por chave SSH
    embed_host: str = "cid-uff.net"
    embed_ssh_port: int = 22024
    embed_ssh_user: str = "marcus"
    embed_model: str = "BAAI/bge-m3"


def sqlite_path(dsn: str) -> str:
    """Extrai o caminho de arquivo de um DSN ``sqlite:///...``.

    ``sqlite:///data/catalog.db`` -> ``data/catalog.db`` (relativo)
    ``sqlite:////abs/catalog.db``  -> ``/abs/catalog.db`` (absoluto)
    """
    prefix = "sqlite:///"
    if not dsn.startswith(prefix):
        raise ValueError(f"DSN não é SQLite: {dsn!r}")
    return dsn[len(prefix) :]
