"""Servidor MCP (FastMCP) do BaseUFF — retrieval-only.

Expõe a busca híbrida (denso+esparso, RRF, com reranker opcional) como tools MCP,
além de documentação rica (``instructions`` + tool ``info``) para que o agente
cliente entenda exatamente o que a base contém e como consultá-la.
"""

from __future__ import annotations

import re
import time
import unicodedata

from fastmcp import FastMCP
from qdrant_client import QdrantClient, models

from .auth import current_agent
from .pii import mask_cpf
from .retriever import QueryEncoder, dossier, get_document, retrieve, snippet_around

SOURCES = {
    "boletim": (
        "Boletins de Serviço da UFF — o diário oficial INTERNO da universidade (1996–2026). "
        "Contém portarias, nomeações, exonerações, designações, PROGRESSÕES e PROMOÇÕES "
        "funcionais de docentes/técnicos, licenças (capacitação, saúde), aposentadorias, "
        "diárias, resoluções dos conselhos (CEPEx, CUV, CEP), editais e resultados de "
        "concurso, convênios e eleições internas. Cada ato tem número do boletim, data e página."
    ),
    "sti_kb": (
        "Base de Conhecimento do STI — MANUAIS e TUTORIAIS dos sistemas da UFF (Administração "
        "Acadêmica, Diploma, etc.), organizados por pasta. Inclui o TEXTO DAS TELAS de sistema "
        "extraído por OCR (menus, botões, campos), então dá para achar um passo pela interface."
    ),
    "pesquisa": (
        "Portal da Pesquisa (pesquisa.uff.br) — editais e notícias da PROPPI: PIBIC, bolsas de "
        "iniciação científica, chamadas internas, cronogramas e resultados. "
        "NÃO é um banco de perfis de pesquisadores."
    ),
    "guia": (
        "Guia do Estudante e da Comunidade (www.uff.br) — TUTORIAIS e serviços voltados a ALUNOS "
        "e ao público: como EMITIR/obter 2ª via de DIPLOMA e histórico, colação de grau, "
        "matrícula, trancamento, carteirinha UFF, bibliotecas, bolsas/auxílios estudantis, "
        "estágios, mobilidade internacional e a Carta de Serviços. Passo a passo, requisitos, "
        "prazos e setor responsável. Use para 'COMO faço X?' do estudante/comunidade "
        "(diferente do sti_kb, que é manual dos SISTEMAS para servidores)."
    ),
    "sbpc": (
        "78ª Reunião Anual da SBPC na UFF (Campus Gragoatá, Niterói, 26/07 a 01/08/2026) + a "
        "SBPC institucional. Contém a PROGRAMAÇÃO CIENTÍFICA completa (mesas-redondas, "
        "conferências, sessões especiais — cada atividade com dia, horário, local, modalidade, "
        "coordenador e palestrantes), minicursos/webminicursos (ementa, público-alvo), caderno "
        "de PÔSTERES (trabalhos aprovados com autores), programações temáticas (SBPC Gênero, "
        "Afro e Indígena, Jovem, Cultural), serviço do evento (inscrição, local, hospedagem, "
        "transporte), notícias e a SBPC em si (história, estatuto, diretoria). Prefira a tool "
        "dedicada `sbpc(query, dia?, tipo?)`."
    ),
}

# Natureza de cada fonte: TUTORIAL (como fazer, passo a passo), DOCUMENTO (ato/registro
# oficial já publicado) ou EVENTO (programação/serviço de evento). O agente deve saber o
# que está lendo — vai em cada resultado (`natureza`).
SOURCE_KIND = {
    "boletim": "documento",
    "resolucao": "documento",
    "pesquisa": "documento",
    "sti_kb": "tutorial",
    "guia": "tutorial",
    "sbpc": "evento",
}
KIND_LABEL = {
    "tutorial": "Tutorial — passo a passo / como fazer",
    "documento": "Documento oficial — ato, registro ou edital já publicado",
    "evento": "Evento — programação e informações de evento (atividade, dia/hora, local)",
}


def natureza(source: str | None) -> str:
    """Classe do conteúdo da fonte: 'tutorial', 'documento' ou 'evento'."""
    return SOURCE_KIND.get(source or "", "documento")


INSTRUCTIONS = (
    "BaseUFF: busca semântica no acervo ABERTO da Universidade Federal Fluminense (UFF).\n\n"
    "Três NATUREZAS de conteúdo — saiba o que está entregando ao usuário (cada resultado traz "
    "o campo `natureza`):\n"
    "- **tutorial** = COMO FAZER, passo a passo (ex.: emitir diploma, usar um sistema). "
    "Responde 'como eu faço X?'.\n"
    "- **documento** = ato/registro/edital OFICIAL já publicado (o que a UFF decidiu/publicou). "
    "Responde 'qual o ato?', 'quando?', 'quem?'. NÃO é instrução de procedimento.\n"
    "- **evento** = programação e informações de EVENTO (atividade com dia/hora/local, "
    "palestrantes, serviço). Responde 'o que tem no dia X?', 'quem participa?'.\n\n"
    "Fontes disponíveis (parâmetro `source` da tool `search`):\n"
    + "\n".join(f"- {k} [{natureza(k)}]: {v}" for k, v in SOURCES.items())
    + "\n\nEstratégia (qual tool usar):\n"
    "- **Como fazer algo** (diploma, matrícula, usar um sistema) → `search(query, source='sti_kb')`: "
    "conteúdo `natureza=tutorial`.\n"
    "- **78ª Reunião Anual da SBPC** (programação por dia/tema, palestrantes, minicursos, "
    "inscrição/local) → `sbpc(query, dia?, tipo?)`: tool DEDICADA ao evento, resultados "
    "estruturados (dia, horário, local, coordenador, palestrantes).\n"
    "- **Tema/assunto** → `search(query, source?, date_from?, date_to?, limit)`: híbrido+reranker; "
    "fixe `source`/período quando souber (boletim domina o acervo).\n"
    "- **Todos os atos de uma PESSOA** (dossiê de progressão, histórico) → `dossie(nome, source)`: "
    "EXAUSTIVO (não top-k), varre todo o acervo, dedup por documento, cronológico.\n"
    "- **Ler um ato/documento inteiro** → `get_documento(doc_id)` (doc_id vem no `search`).\n"
    "- **Dimensão do acervo** → `info()`.\n"
    "- Cada resultado traz citação rastreável: número, data, URL e um trecho, além da `natureza`.\n\n"
    "Limitações (seja honesto com o usuário): só conteúdo PÚBLICO já publicado. NÃO há acesso a "
    "SIAPE/SiapeNet, sistemas internos, dados financeiros detalhados nem cadastro de servidores. "
    "Boletins escaneados antigos podem ter ruído de OCR. "
    "Use a tool `info` para ver a cobertura atual."
)


POSSIBILIDADES = [
    "Levantar TODOS os atos de um servidor/professor (progressões, promoções, designações, "
    "licenças, diárias, aposentadoria) — busque o nome completo em source='boletim'.",
    "Obter o texto de uma portaria/resolução pelo número (ex.: 'Resolução CEPEx 3.779').",
    "Aprender o passo a passo de um sistema da UFF (ex.: 'como preencher o RAD', 'registrar "
    "diploma') em source='sti_kb' — inclui texto das telas (OCR).",
    "Orientar um ALUNO/cidadão em serviços da UFF (emitir 2ª via de diploma, colação de grau, "
    "matrícula, carteirinha, bolsas, estágio) em source='guia' — passo a passo e requisitos.",
    "Consultar editais e bolsas de pesquisa (PIBIC, iniciação científica) em source='pesquisa'.",
    "Montar o roteiro de um participante da 78ª Reunião Anual da SBPC (UFF, 26/07–01/08/2026): "
    "programação por dia/tema, palestrantes, minicursos, pôsteres e serviço do evento — use a "
    "tool sbpc(query, dia?, tipo?).",
    "Buscar por tema livre (ex.: 'convênio', 'afastamento no exterior') em todo o acervo.",
]

CONTATO_EMAIL = "marcusantonio@id.uff.br"

EXEMPLOS = [
    {
        "objetivo": "atos de um professor",
        "chamada": "search('Eduardo Camilo da Silva', source='boletim', limit=10)",
    },
    {
        "objetivo": "como usar o sistema RAD",
        "chamada": "search('preencher RAD atividades de ensino', source='sti_kb')",
    },
    {
        "objetivo": "aluno: emitir 2ª via de diploma",
        "chamada": "search('segunda via de diploma e histórico como solicitar', source='guia')",
    },
    {
        "objetivo": "edital PIBIC",
        "chamada": "search('edital PIBIC bolsa de iniciação científica', source='pesquisa')",
    },
    {
        "objetivo": "programação da SBPC num dia",
        "chamada": "sbpc('inteligência artificial', dia='2026-07-29')",
    },
    {
        "objetivo": "minicursos da SBPC sobre um tema",
        "chamada": "sbpc('meio ambiente e educação', tipo='minicurso')",
    },
]


def _dia_iso(dia: str | None) -> str | None:
    """Normaliza o dia do evento para ISO: aceita "2026-07-29", "29/07" e "29/7/2026"."""
    if not dia:
        return None
    d = dia.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
        return d
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{4}))?", d)
    if m:
        return f"{m.group(3) or '2026'}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    return d


def _tipo_slug(tipo: str | None) -> str | None:
    """Normaliza o tipo: sem acento, minúsculas, espaços → hífen ("Mesa Redonda" → "mesa-redonda")."""
    if not tipo:
        return None
    norm = unicodedata.normalize("NFKD", tipo.lower())
    sem_acento = "".join(c for c in norm if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", sem_acento).strip("-") or None


def build_docs(client: QdrantClient, collection: str, catalog=None) -> dict:
    """Documentação e dimensão do acervo (ao vivo). Pública, sem auth."""
    chunks: dict[str, int] = {}
    for src in SOURCES:
        chunks[src] = client.count(
            collection,
            count_filter=models.Filter(
                must=[models.FieldCondition(key="source", match=models.MatchValue(value=src))]
            ),
        ).count

    cat = catalog.stats() if catalog is not None else {}
    acervo = {}
    for src, descricao in SOURCES.items():
        c = cat.get(src, {})
        acervo[src] = {
            "natureza": natureza(src),
            "tipo": descricao,
            "documentos": c.get("documentos"),
            "trechos_indexados": chunks[src],
            "periodo": [c.get("data_inicial"), c.get("data_final")],
        }

    total_docs = sum(v["documentos"] or 0 for v in acervo.values())
    return {
        "servidor": "BaseUFF — RAG sobre o acervo aberto da Universidade Federal Fluminense",
        "instructions": INSTRUCTIONS,
        "naturezas": KIND_LABEL,
        "acervo": acervo,
        "tamanho": {
            "total_documentos": total_docs,
            "total_trechos_indexados": client.count(collection).count,
        },
        "possibilidades": POSSIBILIDADES,
        "exemplos": EXEMPLOS,
        "tools": {
            "search": "search(query, limit=5, source=None, date_from=None, date_to=None) -> passagens (tema)",
            "sbpc": "sbpc(query, limit=5, dia=None, tipo=None) -> 78ª Reunião Anual da SBPC: atividades com dia/horário/local/palestrantes",
            "dossie": "dossie(nome, source='boletim') -> confirmados + provaveis (exaustivo por pessoa)",
            "get_documento": "get_documento(doc_id) -> documento/ato inteiro",
            "info": "info() -> esta documentação + dimensão do acervo",
        },
        "nao_inclui": [
            "SIAPE/SiapeNet e sistemas internos",
            "dados financeiros detalhados",
            "cadastro de servidores / dados pessoais não publicados",
        ],
        "autenticacao": "as tools exigem header Authorization: Bearer <token>; esta doc é pública",
        "solicitar_acesso": f"Para obter uma chave de acesso, envie e-mail para {CONTATO_EMAIL}",
    }


def render_docs_html(docs: dict) -> str:
    """Renderiza a documentação como uma página wiki (HTML+CSS+JS autocontido)."""
    import html as _h

    acervo = docs.get("acervo", {})
    tam = docs.get("tamanho", {})
    base_url = "https://ultron.cid-uff.net/mcp"

    def esc(x) -> str:
        return _h.escape(str(x if x is not None else "—"))

    fontes_cards = "".join(
        f"""
        <div class="card" id="fonte-{esc(src)}">
          <h3><span class="pill">{esc(src)}</span> <span class="pill kind-{esc(a.get("natureza"))}">{esc(a.get("natureza"))}</span></h3>
          <p>{esc(a.get("tipo"))}</p>
          <div class="meta">
            <span><b>{esc(a.get("documentos"))}</b> documentos</span>
            <span><b>{esc(a.get("trechos_indexados"))}</b> trechos</span>
            <span>período: <b>{esc((a.get("periodo") or [None, None])[0])}</b> → <b>{esc((a.get("periodo") or [None, None])[1])}</b></span>
          </div>
        </div>"""
        for src, a in acervo.items()
    )
    possibilidades = "".join(f"<li>{esc(p)}</li>" for p in docs.get("possibilidades", []))
    exemplos = "".join(
        f"<li><b>{esc(e.get('objetivo'))}:</b> <code>{esc(e.get('chamada'))}</code></li>"
        for e in docs.get("exemplos", [])
    )
    limites = "".join(f"<li>{esc(x)}</li>" for x in docs.get("nao_inclui", []))

    return f"""<!doctype html>
<html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BaseUFF — Servidor MCP</title>
<style>
  :root {{ --bg:#0f1216; --card:#171c22; --fg:#e7edf3; --mut:#9fb0c0; --acc:#3aa0ff; --line:#232c36; }}
  @media (prefers-color-scheme: light) {{ :root {{ --bg:#f6f8fa; --card:#fff; --fg:#1b232b; --mut:#5a6b7b; --acc:#0969da; --line:#e2e8ee; }} }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font:16px/1.55 -apple-system,Segoe UI,Roboto,Arial,sans-serif; background:var(--bg); color:var(--fg); }}
  .wrap {{ max-width:920px; margin:0 auto; padding:32px 20px 80px; }}
  header h1 {{ margin:0 0 4px; font-size:30px; }}
  header p {{ color:var(--mut); margin:0 0 20px; }}
  .stats {{ display:flex; gap:12px; flex-wrap:wrap; margin:18px 0 8px; }}
  .stat {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px 18px; }}
  .stat b {{ font-size:24px; display:block; }}
  .stat span {{ color:var(--mut); font-size:13px; }}
  h2 {{ margin:34px 0 12px; font-size:20px; border-bottom:1px solid var(--line); padding-bottom:6px; }}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px 18px; margin:12px 0; }}
  .card h3 {{ margin:0 0 8px; }}
  .pill {{ background:var(--acc); color:#fff; padding:2px 10px; border-radius:20px; font-size:13px; }}
  .kind-tutorial {{ background:#1baf7a; }}
  .kind-documento {{ background:#4a3aa7; }}
  .kind-evento {{ background:#b45309; }}
  .meta {{ display:flex; gap:18px; flex-wrap:wrap; color:var(--mut); font-size:14px; margin-top:8px; }}
  code {{ background:var(--line); padding:2px 6px; border-radius:6px; font-size:13.5px; }}
  pre {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px; overflow:auto; }}
  ul {{ padding-left:20px; }} li {{ margin:6px 0; }}
  a {{ color:var(--acc); }}
  .foot {{ color:var(--mut); font-size:13px; margin-top:40px; }}
</style></head>
<body><div class="wrap">
<header>
  <h1>BaseUFF <span class="pill">MCP</span></h1>
  <p>{esc(docs.get("servidor"))}</p>
</header>

<div class="stats">
  <div class="stat"><b>{esc(tam.get("total_documentos"))}</b><span>documentos</span></div>
  <div class="stat"><b>{esc(tam.get("total_trechos_indexados"))}</b><span>trechos indexados</span></div>
  <div class="stat"><b>{len(acervo)}</b><span>fontes</span></div>
</div>

<h2>Fontes do acervo</h2>
{fontes_cards}

<h2>Solicitar acesso</h2>
<div class="card">
  <p>As ferramentas de busca exigem um <b>token</b> (uma chave por pessoa/agente). Para obter o seu,
  envie um e-mail para <a href="mailto:{esc(CONTATO_EMAIL)}?subject=Acesso%20MCP%20BaseUFF"><b>{esc(CONTATO_EMAIL)}</b></a>.</p>
</div>

<h2>Como conectar (agentes)</h2>
<p>Servidor MCP over HTTP. Endpoint e autenticação por token Bearer (uma chave por agente):</p>
<pre>URL:    {esc(base_url)}
Header: Authorization: Bearer &lt;seu-token&gt;

# config genérica (Claude Code / SDKs MCP)
{{ "mcpServers": {{ "baseuff": {{
    "url": "{esc(base_url)}",
    "headers": {{ "Authorization": "Bearer &lt;token&gt;" }}
}} }} }}</pre>

<h2>Ferramentas</h2>
<div class="card"><h3><code>search(query, limit=5, source=None, date_from=None, date_to=None)</code></h3>
<p>Busca por <b>tema</b> (denso+esparso, reranqueada por cross-encoder), com filtros de fonte e
período. Retorna passagens com citação (doc_id, numero, data, url, snippet).</p></div>
<div class="card"><h3><code>sbpc(query, limit=5, dia=None, tipo=None)</code></h3>
<p>Busca <b>dedicada à 78ª Reunião Anual da SBPC</b> (UFF, Niterói, 26/07–01/08/2026): programação
científica, minicursos, pôsteres, notícias e a SBPC institucional. Filtros por <b>dia do evento</b>
e <b>tipo</b> (mesa-redonda, conferencia, minicurso, noticia…). Resultados estruturados com dia,
horário, local, coordenador e palestrantes.</p></div>
<div class="card"><h3><code>dossie(nome, source="boletim")</code></h3>
<p><b>Levantamento exaustivo</b> por pessoa/entidade — TODOS os atos (não top-k), deduplicado por
documento e em ordem cronológica. Ideal para dossiê de progressão/histórico.</p></div>
<div class="card"><h3><code>get_documento(doc_id)</code></h3>
<p>Reconstrói um ato/documento <b>inteiro</b> (todos os trechos, em ordem) para contexto pleno.</p></div>
<div class="card"><h3><code>info()</code></h3><p>Esta documentação + dimensão do acervo ao vivo.</p></div>

<h2>Possibilidades</h2>
<ul>{possibilidades}</ul>

<h2>Exemplos</h2>
<ul>{exemplos}</ul>

<h2>Limitações</h2>
<p>Só conteúdo público publicado. Não inclui:</p>
<ul>{limites}</ul>

<p class="foot">Documentação pública. JSON em <a href="{esc(base_url)}/docs">{esc(base_url)}/docs</a>.
As ferramentas exigem token; esta página não.</p>
</div></body></html>"""


def create_app(
    client: QdrantClient,
    collection: str,
    encoder: QueryEncoder,
    reranker=None,
    catalog=None,
    querylog=None,
) -> FastMCP:
    mcp: FastMCP = FastMCP("BaseUFF", instructions=INSTRUCTIONS)

    def _record(tool: str, query: str, t0: float, n_results: int, **extra) -> None:
        if querylog is None:
            return
        querylog.log(
            {
                "agent": current_agent.get(),
                "tool": tool,
                "query": query,
                "n_results": n_results,
                "latency_ms": round((time.perf_counter() - t0) * 1000),
                **extra,
            }
        )

    @mcp.tool
    def search(
        query: str,
        limit: int = 5,
        source: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[dict]:
        """Busca semântica (híbrida + reranker) no acervo aberto da UFF, com filtros.

        Melhor para perguntas por TEMA/assunto. Para levantar TODOS os atos de uma
        pessoa, use `dossie`. Para ler um ato inteiro, use `get_documento`.

        Args:
            query: pergunta/termo em linguagem natural (pt-BR).
            limit: número máximo de passagens (padrão 5).
            source: filtra a fonte — "boletim", "sti_kb", "pesquisa", "guia" ou "sbpc"
                (vazio = todas). Como boletim domina o acervo, fixe a fonte quando souber
                onde procurar. Para a 78ª Reunião Anual da SBPC prefira a tool `sbpc`.
            date_from / date_to: período (ISO "AAAA-MM-DD") sobre a data de publicação.

        Retorna [{doc_id, numero, source, publish_date, url, snippet, score}] por relevância.
        """
        t0 = time.perf_counter()
        results = retrieve(
            client,
            collection,
            encoder,
            query,
            limit=limit,
            source=source,
            date_from=date_from,
            date_to=date_to,
            reranker=reranker,
        )
        out = [
            {
                "doc_id": r.doc_id,
                "numero": r.numero,
                "source": r.source,
                "natureza": natureza(r.source),
                "publish_date": r.publish_date,
                "url": r.url,
                "snippet": mask_cpf(snippet_around(r.text, query)),
                "score": round(r.score, 4),
            }
            for r in results
        ]
        _record(
            "search",
            query,
            t0,
            len(out),
            source=source,
            date_from=date_from,
            date_to=date_to,
            top_results=[{"doc_id": r["doc_id"], "score": r["score"]} for r in out[:3]],
        )
        return out

    @mcp.tool
    def sbpc(
        query: str,
        limit: int = 5,
        dia: str | None = None,
        tipo: str | None = None,
    ) -> list[dict]:
        """Busca DEDICADA à 78ª Reunião Anual da SBPC (UFF, Campus Gragoatá, Niterói,
        26/07 a 01/08/2026) e à SBPC institucional. Resultados estruturados: cada
        atividade traz dia, horário, local, modalidade, coordenador e palestrantes.

        Cobre: programação científica (mesas-redondas, conferências, sessões especiais),
        minicursos/webminicursos (ementa, público-alvo), pôsteres (trabalhos com autores),
        trilhas temáticas (SBPC Gênero, Afro e Indígena, Jovem, Cultural), serviço do
        evento (inscrição, local/mapa, hospedagem, transporte), notícias e a história/
        estrutura da SBPC. Para TODAS as participações de uma pessoa no evento, use
        `dossie(nome, source='sbpc')`.

        Args:
            query: pergunta/termo em linguagem natural (pt-BR).
            limit: número máximo de resultados (padrão 5).
            dia: dia do evento — ISO "2026-07-29" ou "29/07" (26/07 a 01/08/2026). Filtra
                a programação daquele dia; notícias publicadas na mesma data também entram
                (desambigue com `tipo`).
            tipo: tipo de conteúdo — "mesa-redonda", "conferencia", "sessao-especial",
                "encontro", "assembleia", "oficina", "minicurso", "webminicurso",
                "atividade" (solenidades/aberturas), "poster", "programacao-tematica",
                "pagina" (serviço do evento), "institucional" (a SBPC), "noticia",
                "documento" (normas).

        Retorna [{doc_id, titulo, tipo, dia, horario, local, modalidade, coordenador,
        palestrantes, trilha, url, snippet, score, natureza}] por relevância.
        """
        t0 = time.perf_counter()
        d = _dia_iso(dia)
        t = _tipo_slug(tipo)
        results = retrieve(
            client,
            collection,
            encoder,
            query,
            limit=limit,
            source="sbpc",
            date_from=d,
            date_to=d,
            tipo=t,
            reranker=reranker,
        )
        out = []
        for r in results:
            extra = r.extra or {}
            out.append(
                {
                    "doc_id": r.doc_id,
                    "titulo": r.title,
                    "tipo": extra.get("tipo"),
                    "dia": r.publish_date,
                    "horario": extra.get("horario"),
                    "local": extra.get("local"),
                    "modalidade": extra.get("modalidade"),
                    "coordenador": extra.get("coordenador"),
                    "palestrantes": extra.get("palestrantes") or extra.get("ministrantes"),
                    "trilha": extra.get("trilha"),
                    "url": r.url,
                    "snippet": mask_cpf(snippet_around(r.text, query)),
                    "score": round(r.score, 4),
                    "natureza": natureza(r.source),
                }
            )
        _record(
            "sbpc",
            query,
            t0,
            len(out),
            source="sbpc",
            date_from=d,
            date_to=d,
            tipo=t,
            top_results=[{"doc_id": r["doc_id"], "score": r["score"]} for r in out[:3]],
        )
        return out

    @mcp.tool
    def dossie(nome: str, source: str | None = "boletim") -> dict:
        """Levantamento EXAUSTIVO por pessoa/entidade — TODOS os atos, não só o top-k.

        Use para "todos os boletins onde X aparece", montar dossiê de progressão etc.
        Varre todo o acervo, deduplica por documento e ordena por data. Retorna em DOIS níveis:
        - `confirmados`: nome contíguo no texto (alta confiança — é a pessoa).
        - `provaveis`: mesmos nomes em ordem com partes no meio (recupera nomes compostos,
          ex.: "Mariana Marinho Peixoto" acha "Mariana Marinho da Costa Lima Peixoto"). Podem
          conter HOMÔNIMOS — trate como "a verificar". Quanto mais completo o nome, menos ruído.

        Args:
            nome: nome da pessoa/entidade (use o mais completo que souber).
            source: fonte (padrão "boletim"; vazio = todas).

        Retorna {nome, total_confirmados, total_provaveis, confirmados:[...], provaveis:[...]},
        cada item {numero, source, publish_date, url, snippet}.
        """
        t0 = time.perf_counter()
        res = dossier(client, collection, nome, source=source)
        for bucket in ("confirmados", "provaveis"):
            for d in res[bucket]:  # anonimiza o snippet entregue (índice permanece cru)
                d["snippet"] = mask_cpf(d.get("snippet"))
                d["natureza"] = natureza(d.get("source"))
        conf, prov = res["confirmados"], res["provaveis"]
        _record(
            "dossie",
            nome,
            t0,
            len(conf) + len(prov),
            source=source,
            top_results=[
                {"numero": d["numero"], "publish_date": d["publish_date"]} for d in conf[:3]
            ],
        )
        return {
            "nome": nome,
            "total_confirmados": len(conf),
            "total_provaveis": len(prov),
            "confirmados": conf,
            "provaveis": prov,
        }

    @mcp.tool
    def get_documento(
        doc_id: int | None = None, numero: str | None = None, source: str = "boletim"
    ) -> dict | None:
        """Reconstrói um documento/ato INTEIRO (todos os trechos, em ordem) para contexto pleno.

        Prefira `doc_id` (único, vem nos resultados de `search`). `numero` pode ser ambíguo
        entre anos. Retorna {doc_id, source, numero, publish_date, url, n_chunks, texto} ou null.
        """
        t0 = time.perf_counter()
        doc = get_document(client, collection, doc_id=doc_id, numero=numero, source=source)
        if doc:  # anonimiza o texto entregue (índice permanece cru)
            doc["texto"] = mask_cpf(doc["texto"])
            doc["natureza"] = natureza(doc.get("source"))
        _record("get_documento", str(doc_id or numero), t0, 1 if doc else 0, source=source)
        return doc

    @mcp.tool
    def info() -> dict:
        """Documentação do servidor: fontes, o que cada uma contém, cobertura ATUAL
        (contagem de trechos por fonte) e limitações. Chame isto para saber o que a base oferece."""
        return build_docs(client, collection, catalog)

    return mcp
