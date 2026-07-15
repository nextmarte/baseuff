"""Bateria de CASOS DE USO REAIS da fonte sbpc (78ª Reunião Anual da SBPC na UFF).

Simula as perguntas que usuários finais farão a agentes de IA com acesso ao MCP:
programação por dia/tema/tipo, palestrantes, logística do evento, SBPC institucional
e trilhas temáticas. Cada cenário roda pelo MESMO caminho da tool `sbpc` (retrieve com
source='sbpc' + filtros dia/tipo + reranker em cascata) e "acerta" se todos os termos
esperados aparecem no título+texto de um resultado. Cenários `estrutural=True` também
exigem que o topo traga os campos estruturados (title + extra.horario/dia) — o que a
tool devolve como dia/horário/local/coordenador/palestrantes.

Critério de aceite (combinado com o usuário): hit@3 >= 90% dos cenários known-item,
campos estruturados presentes e latência na faixa da produção (~660ms).

    uv run python scripts/eval_sbpc.py             # cascata (igual à produção)
    uv run python scripts/eval_sbpc.py --no-rerank # só híbrido (debug)
"""

from __future__ import annotations

import argparse
import statistics
import time
import unicodedata

from qdrant_client import QdrantClient
from uff_core.config import Settings
from uff_server.encoder import RemoteEncoder
from uff_server.retriever import retrieve


def _fold(s: str | None) -> str:
    norm = unicodedata.normalize("NFKD", (s or "").lower())
    return "".join(c for c in norm if not unicodedata.combining(c))


# (nome, query, dia, tipo, termos-esperados, estrutural)
# estrutural=True: o 1º acerto deve ter campos de atividade (title + extra com horário/dia).
SCENARIOS: list[tuple[str, str, str | None, str | None, list[str], bool]] = [
    # --- 1. programação por dia ---
    ("dia_abertura", "sessão solene de abertura", "2026-07-26", None, ["solene"], True),
    ("dia_cotas", "cotas e equidade nas instituições", "2026-07-29", None, ["cotas"], True),
    ("dia_sem_filtro_data_errada", "sessão solene de abertura", "2026-07-28", None, [], False),
    # --- 2. programação por tema ---
    ("tema_ia", "inteligência artificial", None, None, ["inteligencia artificial"], False),
    ("tema_juventude", "saúde mental da juventude", None, None, ["juventude"], True),
    ("tema_biomas", "biomas brasileiros bioeconomia", None, None, ["biomas"], True),
    (
        "tema_universidade_indigena",
        "universidade indígena no Brasil",
        None,
        None,
        ["indigena"],
        True,
    ),
    # --- 3. por tipo ---
    (
        "tipo_conferencia",
        "Marie Curie visita ao Brasil",
        None,
        "conferencia",
        ["marie curie"],
        True,
    ),
    (
        "tipo_minicurso_ia",
        "IA generativa na educação básica",
        None,
        "minicurso",
        ["generativa"],
        False,
    ),
    ("tipo_webminicurso", "HPLC cromatografia", None, "webminicurso", ["hplc"], False),
    ("tipo_oficina", "criação de conteúdo no TikTok", None, "oficina", ["tiktok"], True),
    ("tipo_errado_vazio", "Marie Curie visita ao Brasil", None, "minicurso", [], False),
    # --- 4. pessoas (coordenador/palestrante; exaustivo é o dossie) ---
    ("pessoa_ildeu", "Ildeu de Castro Moreira", None, None, ["ildeu"], True),
    ("pessoa_coordenadora", "Ana Paula da Silva UFF cotas", None, None, ["ana paula"], True),
    # --- 5. logística / participação ---
    ("log_local", "onde acontece o evento local e mapa", None, None, ["gragoata"], False),
    ("log_inscricao", "como se inscrever na reunião anual", None, None, ["inscri"], False),
    ("log_hospedagem", "hospedagem alimentação e transporte", None, None, ["hospedagem"], False),
    ("log_posteres_normas", "normas para apresentação de pôster", None, None, ["poster"], False),
    ("log_imprensa", "credenciamento de imprensa", None, None, ["imprensa"], False),
    # --- 6. SBPC institucional ---
    ("inst_o_que_e", "o que é a SBPC quem somos", None, None, ["progresso da ciencia"], False),
    ("inst_historia", "história da SBPC desde 1948", None, None, ["histor"], False),
    ("inst_diretoria", "diretoria da SBPC gestão atual", None, None, ["diretoria"], False),
    # --- 7. trilhas temáticas / programações especiais ---
    ("trilha_genero", "programação SBPC Gênero", None, None, ["genero"], False),
    ("trilha_afroindigena", "programação afro e indígena", None, None, ["afro"], False),
    ("trilha_jovem", "SBPC Jovem atividades para estudantes", None, None, ["jovem"], False),
    ("trilha_cultural", "SBPC Cultural música teatro cinema", None, None, ["cultural"], False),
    # --- 8. notícias da edição ---
    (
        "noticia_programacao",
        "programação científica reúne mais de 220 atividades",
        None,
        "noticia",
        ["220"],
        False,
    ),
    # --- negativo: fora do domínio ---
    (
        "neg_absurdo",
        "kubernetes container orchestration helm chart",
        None,
        None,
        ["kubernetes"],
        False,
    ),
]

TOP_K = 10


def _match(hit, expected: list[str]) -> bool:
    alvo = _fold((hit.title or "") + " " + (hit.text or ""))
    return all(_fold(t) in alvo for t in expected)


def _rank(hits, expected: list[str]) -> int | None:
    for i, h in enumerate(hits, 1):
        if _match(h, expected):
            return i
    return None


def _tem_estrutura(hit) -> bool:
    extra = hit.extra or {}
    return bool(hit.title) and bool(hit.publish_date) and "horario" in extra


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-rerank", action="store_true", help="só híbrido, sem cascata")
    ap.add_argument("--limit", type=int, default=TOP_K)
    args = ap.parse_args()

    s = Settings()
    client = QdrantClient(url=s.qdrant_url, timeout=30)
    encoder = RemoteEncoder(s.encoder_url)
    reranker = None
    if not args.no_rerank:
        from uff_server.reranker import CascadeReranker, ColbertReranker, RemoteReranker

        reranker = CascadeReranker(ColbertReranker(s.encoder_url), RemoteReranker(s.encoder_url))
    print(f"### sbpc — casos de uso reais ({'cascata' if reranker else 'híbrido'}) ###")

    ranks: list[int | None] = []
    lat: list[float] = []
    estrutura_ok = estrutura_n = 0
    print(f"{'cenário':28} {'dia':11} {'tipo':13} {'rank':>5} {'ms':>6}")
    print("-" * 78)
    for name, query, dia, tipo, expected, estrutural in SCENARIOS:
        t0 = time.perf_counter()
        hits = retrieve(
            client,
            s.qdrant_collection,
            encoder,
            query,
            limit=args.limit,
            source="sbpc",
            date_from=dia,
            date_to=dia,
            tipo=tipo,
            reranker=reranker,
        )
        lat.append((time.perf_counter() - t0) * 1000)
        negative = name.startswith("neg_") or not expected
        r = _rank(hits, expected) if expected else (1 if hits else None)
        ok = (r is None) if negative else (r is not None)
        if estrutural and r:
            estrutura_n += 1
            hit = hits[r - 1]
            if _tem_estrutura(hit):
                estrutura_ok += 1
            else:
                ok = False
        ranks.append(None if negative else r)
        mark = "OK " if ok else "!! "
        print(
            f"{mark}{name:26} {dia or '-':11} {tipo or '-':13} {str(r or '-'):>5} {lat[-1]:>6.0f}"
        )

    graded = [
        r
        for (n, _, _, _, exp, _), r in zip(SCENARIOS, ranks, strict=False)
        if exp and not n.startswith("neg_")
    ]
    hit1 = sum(1 for r in graded if r == 1)
    hit3 = sum(1 for r in graded if r and r <= 3)
    hitk = sum(1 for r in graded if r is not None)
    mrr = sum((1.0 / r) for r in graded if r) / len(graded)
    n = len(graded)
    print("-" * 78)
    print(
        f"known-item: {n} | hit@1={hit1}/{n} hit@3={hit3}/{n} ({100 * hit3 / n:.0f}%) "
        f"hit@{args.limit}={hitk}/{n} | MRR={mrr:.3f}"
    )
    print(f"campos estruturados no acerto: {estrutura_ok}/{estrutura_n}")
    print(
        f"latência/consulta: média={statistics.mean(lat):.0f}ms "
        f"mediana={statistics.median(lat):.0f}ms max={max(lat):.0f}ms"
    )
    if n and hit3 / n < 0.9:
        print("!! ABAIXO do critério de aceite (hit@3 >= 90%) — iterar antes de concluir.")


if __name__ == "__main__":
    main()
