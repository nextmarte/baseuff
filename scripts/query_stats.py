"""Analytics da base de consultas (data/queries.db) — gestão de qualidade + dados p/ paper.

Relatório: volume, uso por tool/agente/fonte/dia, distribuição de latência e — o mais
útil para qualidade — as **lacunas** (dossiê/documento sem resultado e buscas cujo melhor
score ficou baixo, sinal de conteúdo faltante ou query mal atendida).

    uv run python scripts/query_stats.py            # relatório
    uv run python scripts/query_stats.py --anon      # anonimiza agentes (p/ publicar)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import statistics
from pathlib import Path

from uff_core.config import Settings

# Abaixo deste score de reranker (sigmoid 0..1), a busca provavelmente não achou bom match.
# ~0.5 é o "neutro" do cross-encoder; ajuste conforme os dados reais forem chegando.
LOW_SCORE = 0.55


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = min(len(s) - 1, int(round((p / 100) * (len(s) - 1))))
    return s[k]


def _anon(agent: str) -> str:
    return "agente_" + hashlib.sha256(agent.encode()).hexdigest()[:8]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None, help="caminho do queries.db")
    ap.add_argument("--anon", action="store_true", help="anonimiza nomes de agente")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--min-score", type=float, default=LOW_SCORE, help="limiar de lacuna")
    args = ap.parse_args()

    db = args.db or str(Path(Settings().data_dir) / "queries.db")
    if not Path(db).exists():
        print(f"sem base de consultas em {db} (nenhuma consulta ainda)")
        return
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM queries").fetchall()]
    conn.close()
    if not rows:
        print("base de consultas vazia")
        return

    n = len(rows)
    lat = [r["latency_ms"] for r in rows if r["latency_ms"] is not None]
    print(f"=== BaseUFF — analytics de consultas ({n} consultas) ===")
    print(f"período: {rows[0]['ts']} … {rows[-1]['ts']}")
    print(
        f"latência: p50={_pct(lat, 50):.0f}ms p95={_pct(lat, 95):.0f}ms "
        f"média={statistics.mean(lat):.0f}ms max={max(lat):.0f}ms"
    )

    def dist(key, transform=lambda x: x):
        d: dict = {}
        for r in rows:
            d[transform(r.get(key))] = d.get(transform(r.get(key)), 0) + 1
        return sorted(d.items(), key=lambda kv: kv[1], reverse=True)

    print("\npor tool:      " + ", ".join(f"{k}={v}" for k, v in dist("tool")))
    agent_key = (lambda a: _anon(a or "?")) if args.anon else (lambda a: a or "?")
    print("por agente:    " + ", ".join(f"{agent_key(k)}={v}" for k, v in dist("agent")))
    print("por fonte:     " + ", ".join(f"{k or 'todas'}={v}" for k, v in dist("source")))
    por_dia = dist("ts", lambda t: (t or "")[:10])
    print("por dia:       " + ", ".join(f"{k}={v}" for k, v in por_dia))

    print(f"\ntop {args.top} queries:")
    for q, c in dist("query")[: args.top]:
        print(f"  {c:3}x  {q}")

    # --- lacunas (sinal de qualidade) ---
    gaps = []
    for r in rows:
        if r["tool"] in ("dossie", "get_documento") and (r["n_results"] or 0) == 0:
            gaps.append((r["tool"], r["query"], "0 resultados"))
        elif r["tool"] == "search":
            top = json.loads(r["top_results"] or "[]")
            best = max((t.get("score", 0) for t in top), default=0)
            if best < args.min_score:
                gaps.append(("search", r["query"], f"melhor score {best:.2f}"))
    print(f"\nLACUNAS ({len(gaps)}) — consultas mal atendidas (conteúdo faltante?):")
    for tool, q, motivo in gaps[: args.top]:
        print(f"  [{tool}] {q}  ({motivo})")


if __name__ == "__main__":
    main()
