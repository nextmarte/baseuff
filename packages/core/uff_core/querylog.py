"""Base de consultas do MCP (SQLite) — gestão de qualidade + dados para paper.

Registra cada chamada de tool de busca (search/dossie/get_documento) com agente,
query, filtros, nº de resultados, latência e o topo dos resultados. Habilita
analytics de uso e de lacunas (queries com 0 resultados).

Thread-safe por construção: o servidor MCP chama `log()` em threads de worker e uma
conexão SQLite só pode ser usada na thread que a criou — então cada escrita/leitura
abre sua própria conexão de curta duração. WAL permite leituras concorrentes.
"""

from __future__ import annotations

import json
import sqlite3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS queries (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL DEFAULT (datetime('now')),
    agent        TEXT,
    tool         TEXT NOT NULL,
    query        TEXT,
    source       TEXT,
    date_from    TEXT,
    date_to      TEXT,
    n_results    INTEGER,
    latency_ms   INTEGER,
    top_results  TEXT NOT NULL DEFAULT '[]',
    error        TEXT
);
CREATE INDEX IF NOT EXISTS idx_queries_ts    ON queries (ts);
CREATE INDEX IF NOT EXISTS idx_queries_agent ON queries (agent);
CREATE INDEX IF NOT EXISTS idx_queries_tool  ON queries (tool);
"""

_FIELDS = (
    "agent",
    "tool",
    "query",
    "source",
    "date_from",
    "date_to",
    "n_results",
    "latency_ms",
    "top_results",
    "error",
)


class QueryLog:
    def __init__(self, path: str = "data/queries.db") -> None:
        self._path = path
        conn = self._connect()
        try:
            conn.executescript(_SCHEMA)
            conn.commit()
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def log(self, entry: dict) -> None:
        """Registra uma consulta. Nunca deve derrubar a tool — erros são engolidos."""
        row = {k: entry.get(k) for k in _FIELDS}
        row["top_results"] = json.dumps(entry.get("top_results") or [], ensure_ascii=False)
        try:
            conn = self._connect()
            try:
                conn.execute(
                    f"INSERT INTO queries ({', '.join(_FIELDS)}) "
                    f"VALUES ({', '.join(':' + f for f in _FIELDS)})",
                    row,
                )
                conn.commit()
            finally:
                conn.close()
        except sqlite3.Error:
            pass  # logging nunca pode quebrar a consulta do usuário

    def recent(self, limit: int = 50) -> list[dict]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM queries ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        finally:
            conn.close()
        out = []
        for r in rows:
            d = dict(r)
            d["top_results"] = json.loads(d.get("top_results") or "[]")
            out.append(d)
        return out
