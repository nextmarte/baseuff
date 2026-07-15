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


def _percentis(values: list[float]) -> dict:
    if not values:
        return {"p50": 0, "p95": 0, "max": 0, "media": 0}
    s = sorted(values)

    def pct(p: float) -> int:
        return int(s[min(len(s) - 1, round((p / 100) * (len(s) - 1)))])

    return {"p50": pct(50), "p95": pct(95), "max": int(max(s)), "media": int(sum(s) / len(s))}


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
    tipo         TEXT,
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
    "tipo",
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
            # migração: bases criadas antes da coluna `tipo` (filtro da tool sbpc)
            try:
                conn.execute("ALTER TABLE queries ADD COLUMN tipo TEXT")
            except sqlite3.OperationalError:
                pass  # coluna já existe
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

    def aggregates(self) -> dict:
        """Métricas agregadas para o painel (por dia/tool/agente/fonte, latência, lacunas)."""
        conn = self._connect()
        try:

            def grp(col: str) -> list[list]:
                rows = conn.execute(
                    f"SELECT COALESCE({col},'—') k, COUNT(*) n FROM queries "
                    f"GROUP BY k ORDER BY n DESC"
                ).fetchall()
                return [[r["k"], r["n"]] for r in rows]

            total = conn.execute("SELECT COUNT(*) n FROM queries").fetchone()["n"]
            lat = [
                r["latency_ms"]
                for r in conn.execute(
                    "SELECT latency_ms FROM queries WHERE latency_ms IS NOT NULL"
                ).fetchall()
            ]
            per_day = [
                [r["d"], r["n"]]
                for r in conn.execute(
                    "SELECT substr(ts,1,10) d, COUNT(*) n FROM queries GROUP BY d ORDER BY d"
                ).fetchall()
            ]
            agentes = conn.execute("SELECT COUNT(DISTINCT agent) n FROM queries").fetchone()["n"]
            erros = conn.execute(
                "SELECT COUNT(*) n FROM queries WHERE error IS NOT NULL"
            ).fetchone()["n"]
            # lacunas: dossiê/get sem resultado, ou busca cujo melhor score < 0.55
            gaps = conn.execute(
                "SELECT COUNT(*) n FROM queries WHERE "
                "(tool IN ('dossie','get_documento') AND n_results=0)"
            ).fetchone()["n"]
            periodo = conn.execute("SELECT MIN(ts) a, MAX(ts) b FROM queries").fetchone()
            return {
                "total": total,
                "agentes": agentes,
                "erros": erros,
                "lacunas": gaps,
                "periodo": [periodo["a"], periodo["b"]],
                "latencia": _percentis(lat),
                "por_dia": per_day,
                "por_tool": grp("tool"),
                "por_agente": grp("agent"),
                "por_fonte": grp("source"),
            }
        finally:
            conn.close()

    def get(self, qid: int) -> dict | None:
        """Uma consulta pelo id (query CRUA, para re-executar no painel). None se não existir."""
        conn = self._connect()
        try:
            r = conn.execute("SELECT * FROM queries WHERE id = ?", (qid,)).fetchone()
        finally:
            conn.close()
        if r is None:
            return None
        d = dict(r)
        d["top_results"] = json.loads(d.get("top_results") or "[]")
        return d

    def detail(self, kind: str, limit: int = 200) -> list[dict]:
        """Lista de consultas por categoria para o drill-down do painel:
        ``lacunas`` (dossiê/doc 0 resultados), ``erros``, ``lentas`` (mais lentas)."""
        cols = "id,ts,agent,tool,query,source,date_from,date_to,n_results,latency_ms,error"
        sql = {
            "lacunas": f"SELECT {cols} FROM queries WHERE tool IN ('dossie','get_documento') "
            f"AND n_results=0 ORDER BY id DESC LIMIT ?",
            "erros": f"SELECT {cols} FROM queries WHERE error IS NOT NULL ORDER BY id DESC LIMIT ?",
            "lentas": f"SELECT {cols} FROM queries ORDER BY latency_ms DESC LIMIT ?",
        }.get(kind)
        if not sql:
            return []
        conn = self._connect()
        try:
            rows = conn.execute(sql, (limit,)).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]

    def page(
        self, limit: int = 25, offset: int = 0, agent: str | None = None, tool: str | None = None
    ) -> tuple[int, list[dict]]:
        """Página de consultas (mais recentes primeiro) com filtro opcional por agente/tool."""
        where, params = [], []
        if agent:
            where.append("agent = ?")
            params.append(agent)
        if tool:
            where.append("tool = ?")
            params.append(tool)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        conn = self._connect()
        try:
            total = conn.execute(f"SELECT COUNT(*) n FROM queries{clause}", params).fetchone()["n"]
            rows = conn.execute(
                "SELECT id,ts,agent,tool,query,source,date_from,date_to,n_results,latency_ms,error "
                f"FROM queries{clause} ORDER BY id DESC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        finally:
            conn.close()
        return total, [dict(r) for r in rows]
