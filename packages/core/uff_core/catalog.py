"""Catálogo de documentos (SQLite).

Fonte de verdade do estado da ingestão: rastreia cada documento do acervo, sua
chave natural (``source``, ``url``), metadados e status no ciclo de vida. Habilita
deduplicação e ingestão incremental (via ``checksum``/``etag``/``last_modified``).

Backend SQLite por simplicidade no dev/local; a API é pensada para migrar a
Postgres em produção multi-host (crawler+serving no ultron; embed no skynet01).
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from collections.abc import Iterable

from uff_core.schemas import DocStatus, Document, Source

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source        TEXT NOT NULL,
    url           TEXT NOT NULL,
    title         TEXT,
    numero        TEXT,
    publish_date  TEXT,
    orgao         TEXT,
    content_type  TEXT,
    checksum      TEXT,
    etag          TEXT,
    last_modified TEXT,
    status        TEXT NOT NULL DEFAULT 'discovered',
    extra         TEXT NOT NULL DEFAULT '{}',
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (source, url)
);
CREATE INDEX IF NOT EXISTS idx_documents_status ON documents (status);
"""

_COLUMNS = (
    "id, source, url, title, numero, publish_date, orgao, content_type, "
    "checksum, etag, last_modified, status, extra"
)


class Catalog:
    """Acesso ao catálogo de documentos."""

    def __init__(self, path: str = "data/catalog.db") -> None:
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- escrita ---------------------------------------------------------------

    def upsert(self, doc: Document) -> Document:
        """Insere ou atualiza por (source, url), preservando id e status.

        Redescobrir um documento atualiza seus metadados mas **não** rebaixa o
        status já avançado (ex.: um doc ``indexed`` continua ``indexed``).
        """
        self._conn.execute(
            """
            INSERT INTO documents
                (source, url, title, numero, publish_date, orgao, content_type,
                 checksum, etag, last_modified, status, extra)
            VALUES (:source, :url, :title, :numero, :publish_date, :orgao,
                    :content_type, :checksum, :etag, :last_modified, :status, :extra)
            ON CONFLICT (source, url) DO UPDATE SET
                title         = excluded.title,
                numero        = excluded.numero,
                publish_date  = excluded.publish_date,
                orgao         = excluded.orgao,
                content_type  = excluded.content_type,
                checksum      = excluded.checksum,
                etag          = excluded.etag,
                last_modified = excluded.last_modified,
                extra         = excluded.extra,
                updated_at    = datetime('now')
            """,
            self._to_row(doc),
        )
        self._conn.commit()
        saved = self.get_by_url(doc.source, doc.url)
        assert saved is not None  # acabou de ser inserido/atualizado
        return saved

    def set_status(self, doc_id: int, status: DocStatus) -> None:
        self._conn.execute(
            "UPDATE documents SET status = ?, updated_at = datetime('now') WHERE id = ?",
            (status.value, doc_id),
        )
        self._conn.commit()

    def record_fetch(
        self,
        doc_id: int,
        *,
        status: DocStatus,
        checksum: str | None = None,
        etag: str | None = None,
        last_modified: str | None = None,
        content_type: str | None = None,
    ) -> None:
        """Registra o resultado do download de um binário e move o status."""
        self._conn.execute(
            """
            UPDATE documents SET
                checksum      = COALESCE(?, checksum),
                etag          = COALESCE(?, etag),
                last_modified = COALESCE(?, last_modified),
                content_type  = COALESCE(?, content_type),
                status        = ?,
                updated_at    = datetime('now')
            WHERE id = ?
            """,
            (checksum, etag, last_modified, content_type, status.value, doc_id),
        )
        self._conn.commit()

    # -- leitura ---------------------------------------------------------------

    def get(self, doc_id: int) -> Document | None:
        row = self._conn.execute(
            f"SELECT {_COLUMNS} FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
        return self._from_row(row) if row else None

    def get_by_url(self, source: Source, url: str) -> Document | None:
        row = self._conn.execute(
            f"SELECT {_COLUMNS} FROM documents WHERE source = ? AND url = ?",
            (source.value, url),
        ).fetchone()
        return self._from_row(row) if row else None

    def list_by_status(self, status: DocStatus) -> list[Document]:
        rows = self._conn.execute(
            f"SELECT {_COLUMNS} FROM documents WHERE status = ? ORDER BY id",
            (status.value,),
        ).fetchall()
        return [self._from_row(r) for r in rows]

    def count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0])

    def stats(self) -> dict[str, dict]:
        """Resumo do acervo por fonte: nº de documentos e período (min/max publish_date)."""
        rows = self._conn.execute(
            "SELECT source, COUNT(*) n, MIN(publish_date) mn, MAX(publish_date) mx "
            "FROM documents GROUP BY source ORDER BY source"
        ).fetchall()
        return {
            r["source"]: {
                "documentos": r["n"],
                "data_inicial": r["mn"],
                "data_final": r["mx"],
            }
            for r in rows
        }

    # -- (de)serialização ------------------------------------------------------

    @staticmethod
    def _to_row(doc: Document) -> dict[str, object]:
        return {
            "source": doc.source.value,
            "url": doc.url,
            "title": doc.title,
            "numero": doc.numero,
            "publish_date": doc.publish_date.isoformat() if doc.publish_date else None,
            "orgao": doc.orgao,
            "content_type": doc.content_type,
            "checksum": doc.checksum,
            "etag": doc.etag,
            "last_modified": doc.last_modified,
            "status": doc.status.value,
            "extra": json.dumps(doc.extra, ensure_ascii=False),
        }

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Document:
        publish_date = dt.date.fromisoformat(row["publish_date"]) if row["publish_date"] else None
        return Document(
            id=row["id"],
            source=Source(row["source"]),
            url=row["url"],
            title=row["title"],
            numero=row["numero"],
            publish_date=publish_date,
            orgao=row["orgao"],
            content_type=row["content_type"],
            checksum=row["checksum"],
            etag=row["etag"],
            last_modified=row["last_modified"],
            status=DocStatus(row["status"]),
            extra=json.loads(row["extra"]),
        )

    # -- utilitários -----------------------------------------------------------

    def upsert_many(self, docs: Iterable[Document]) -> list[Document]:
        return [self.upsert(d) for d in docs]
