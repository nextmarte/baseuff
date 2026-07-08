"""Harness de avaliação do RAG (known-item + negativos), por base.

Cada cenário tem uma query e termos esperados; um resultado "acerta" se todos os
termos aparecem no seu texto. Reporta o rank do primeiro acerto por cenário e
agrega hit@1/@3/@10 e MRR. Serve para medir antes/depois de mudanças (reranker etc).

    uv run python scripts/eval.py                 # baseline (híbrido)
    uv run python scripts/eval.py --rerank        # com reranker (quando disponível)
"""

from __future__ import annotations

import argparse
import statistics
import time

from qdrant_client import QdrantClient
from uff_core.config import Settings
from uff_server.encoder import RemoteEncoder
from uff_server.retriever import retrieve

# (nome, query, source, termos-esperados-todos-presentes)  — None em source = todas
SCENARIOS: list[tuple[str, str, str | None, list[str]]] = [
    # --- Boletim: known-item por pessoa e por tipo de ato ---
    (
        "bs_pessoa_promocao",
        "promoção funcional classe E Aurelio Lamare Murta",
        "boletim",
        ["aurelio", "classe e"],
    ),
    (
        "bs_aposentadoria",
        "aposentadoria compulsória por idade",
        "boletim",
        ["aposentadoria compulsória"],
    ),
    (
        "bs_licenca_cap",
        "licença para capacitação de servidor",
        "boletim",
        ["licença", "capacitação"],
    ),
    (
        "bs_nomeacao",
        "nomeação de professor aprovado em concurso público",
        "boletim",
        ["nomear", "concurso"],
    ),
    ("bs_afastamento", "afastamento de docente no exterior", "boletim", ["afastamento"]),
    ("bs_resolucao_cepex", "resolução CEPEx sobre progressão docente", "boletim", ["cepex"]),
    # --- Portal da Pesquisa ---
    ("pq_pibic", "edital PIBIC bolsa de iniciação científica", "pesquisa", ["inicia"]),
    ("pq_proppi", "chamada PROPPI pró-reitoria de pesquisa", "pesquisa", ["pesquisa"]),
    # --- STI KB (tutoriais) incl. texto de tela (OCR) ---
    ("sti_diploma", "como registrar diploma no sistema", "sti_kb", ["diploma"]),
    ("sti_apostila_ocr", "apostilamento e rescisão contratual no sistema", "sti_kb", ["apostila"]),
    ("sti_matricula", "recuperação de matrícula do aluno", "sti_kb", ["matr"]),
    # --- Negativo: termo garantidamente ausente do acervo administrativo da UFF ---
    ("neg_absurdo", "kubernetes container orchestration helm chart", None, ["kubernetes"]),
]

TOP_K = 10


def _rank(hits, expected: list[str]) -> int | None:
    for i, h in enumerate(hits, 1):
        text = (h.text or "").lower()
        if all(term in text for term in expected):
            return i
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rerank", action="store_true", help="cross-encoder (BGE-reranker)")
    ap.add_argument("--colbert", action="store_true", help="late-interaction ColBERT (MaxSim)")
    ap.add_argument("--cascade", action="store_true", help="ColBERT -> cross-encoder (recomendado)")
    ap.add_argument("--limit", type=int, default=TOP_K)
    args = ap.parse_args()

    s = Settings()
    client = QdrantClient(url=s.qdrant_url, timeout=30)
    encoder = RemoteEncoder(s.encoder_url)
    reranker = None
    if args.cascade:
        from uff_server.reranker import CascadeReranker, ColbertReranker, RemoteReranker

        reranker = CascadeReranker(ColbertReranker(s.encoder_url), RemoteReranker(s.encoder_url))
        label = "CASCATA (ColBERT -> cross-encoder)"
    elif args.colbert:
        from uff_server.reranker import ColbertReranker

        reranker = ColbertReranker(s.encoder_url)
        label = "COM COLBERT (late-interaction)"
    elif args.rerank:
        from uff_server.reranker import RemoteReranker

        reranker = RemoteReranker(s.encoder_url)
        label = "COM RERANKER (cross-encoder)"
    else:
        label = "BASELINE (híbrido)"
    print(f"### {label} ###")

    ranks: list[int | None] = []
    lat: list[float] = []
    print(f"{'cenário':22} {'fonte':9} {'rank':>5} {'ms':>6}  resultado")
    print("-" * 76)
    for name, query, source, expected in SCENARIOS:
        t0 = time.perf_counter()
        hits = retrieve(
            client,
            s.qdrant_collection,
            encoder,
            query,
            limit=args.limit,
            source=source,
            reranker=reranker,
        )
        lat.append((time.perf_counter() - t0) * 1000)
        r = _rank(hits, expected)
        negative = name.startswith("neg_")
        ok = (r is None) if negative else (r is not None)
        mark = "OK " if ok else "!! "
        ranks.append(None if negative else r)
        print(
            f"{mark}{name:20} {source or 'todas':9} {str(r or '-'):>5} {lat[-1]:>6.0f}  "
            f"{'(esperado: nenhum)' if negative else expected}"
        )

    names = [s[0] for s in SCENARIOS]
    graded = [r for n, r in zip(names, ranks, strict=False) if not n.startswith("neg_")]
    hit1 = sum(1 for r in graded if r == 1)
    hit3 = sum(1 for r in graded if r and r <= 3)
    hit10 = sum(1 for r in graded if r is not None)
    mrr = sum((1.0 / r) for r in graded if r) / len(graded)
    n = len(graded)
    print("-" * 76)
    print(
        f"known-item: {n} cenários | hit@1={hit1}/{n} hit@3={hit3}/{n} "
        f"hit@10={hit10}/{n} | MRR={mrr:.3f}"
    )
    print(
        f"latência/consulta: média={statistics.mean(lat):.0f}ms "
        f"mediana={statistics.median(lat):.0f}ms max={max(lat):.0f}ms"
    )


if __name__ == "__main__":
    main()
