"""Cria índices de payload no Qdrant para habilitar filtros e busca exata (dossiê)
SEM re-embeddar os 392k trechos:

  - text         -> full-text (MatchText): busca exata por nome/termo (modo dossiê)
  - source       -> keyword: filtro por fonte (mais rápido)
  - publish_date -> datetime: filtro por período (date_from/date_to)
  - doc_id       -> integer: agrupar/recuperar um documento inteiro (get_documento)
  - tipo         -> keyword: filtro por tipo de conteúdo (tool sbpc: mesa-redonda, minicurso…)

Idempotente: recriar um índice existente é no-op (só loga). Uso:

    uv run python scripts/reindex_payload.py
"""

from __future__ import annotations

from qdrant_client import QdrantClient, models
from uff_core.config import Settings


def ensure_index(client: QdrantClient, coll: str, field: str, schema, label: str) -> None:
    try:
        client.create_payload_index(coll, field_name=field, field_schema=schema)
        print(f"[idx] {field:14} -> {label}: OK")
    except Exception as exc:  # noqa: BLE001 — já existe / versão: apenas registra
        print(f"[idx] {field:14} -> {label}: {type(exc).__name__} ({exc})")


def main() -> None:
    s = Settings()
    client = QdrantClient(url=s.qdrant_url, timeout=180)
    coll = s.qdrant_collection

    ensure_index(
        client,
        coll,
        "text",
        models.TextIndexParams(
            type=models.TextIndexType.TEXT,
            tokenizer=models.TokenizerType.WORD,
            min_token_len=2,
            max_token_len=30,
            lowercase=True,
        ),
        "full-text (MatchText)",
    )
    ensure_index(client, coll, "source", models.PayloadSchemaType.KEYWORD, "keyword")
    ensure_index(client, coll, "publish_date", models.PayloadSchemaType.DATETIME, "datetime")
    ensure_index(client, coll, "doc_id", models.PayloadSchemaType.INTEGER, "integer")
    ensure_index(client, coll, "numero", models.PayloadSchemaType.KEYWORD, "keyword")
    ensure_index(client, coll, "tipo", models.PayloadSchemaType.KEYWORD, "keyword")

    schema = client.get_collection(coll).payload_schema
    print("\n[idx] payload_schema atual:")
    for field, info in (schema or {}).items():
        print(f"       {field}: {getattr(info, 'data_type', info)}")


if __name__ == "__main__":
    main()
