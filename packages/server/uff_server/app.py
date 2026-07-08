"""Servidor MCP (FastMCP) do BaseUFF — retrieval-only.

Expõe a busca híbrida (denso+esparso, RRF, com reranker opcional) como tools MCP,
além de documentação rica (``instructions`` + tool ``info``) para que o agente
cliente entenda exatamente o que a base contém e como consultá-la.
"""

from __future__ import annotations

from fastmcp import FastMCP
from qdrant_client import QdrantClient, models

from .retriever import QueryEncoder, retrieve

SOURCES = {
    "boletim": (
        "Boletins de Serviço da UFF — o diário oficial INTERNO da universidade (2010–2026). "
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
}

INSTRUCTIONS = (
    "BaseUFF: busca semântica no acervo ABERTO da Universidade Federal Fluminense (UFF).\n\n"
    "Fontes disponíveis (parâmetro `source` da tool `search`):\n"
    + "\n".join(f"- {k}: {v}" for k, v in SOURCES.items())
    + "\n\nComo usar:\n"
    "- `search(query, source=None, limit=5)`: query em linguagem natural (pt-BR). Deixe "
    '`source` vazio para buscar em todas, ou fixe uma fonte ("boletim"/"sti_kb"/"pesquisa").\n'
    "- Cada resultado traz citação rastreável: número, data, URL do PDF e um trecho.\n"
    "- Para uma PESSOA (ex.: progressão de um professor), use o nome completo; os resultados "
    "trazem o boletim/data/URL exatos de cada ato.\n"
    '- Para uma norma específica, cite o número (ex.: "Resolução CEPEx 3.779").\n\n'
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
    "Consultar editais e bolsas de pesquisa (PIBIC, iniciação científica) em source='pesquisa'.",
    "Buscar por tema livre (ex.: 'convênio', 'afastamento no exterior') em todo o acervo.",
]

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
        "objetivo": "edital PIBIC",
        "chamada": "search('edital PIBIC bolsa de iniciação científica', source='pesquisa')",
    },
]


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
            "tipo": descricao,
            "documentos": c.get("documentos"),
            "trechos_indexados": chunks[src],
            "periodo": [c.get("data_inicial"), c.get("data_final")],
        }

    total_docs = sum(v["documentos"] or 0 for v in acervo.values())
    return {
        "servidor": "BaseUFF — RAG sobre o acervo aberto da Universidade Federal Fluminense",
        "instructions": INSTRUCTIONS,
        "acervo": acervo,
        "tamanho": {
            "total_documentos": total_docs,
            "total_trechos_indexados": client.count(collection).count,
        },
        "possibilidades": POSSIBILIDADES,
        "exemplos": EXEMPLOS,
        "tools": {
            "search": "search(query, limit=5, source=None) -> passagens com citação",
            "info": "info() -> esta documentação + dimensão do acervo",
        },
        "nao_inclui": [
            "SIAPE/SiapeNet e sistemas internos",
            "dados financeiros detalhados",
            "cadastro de servidores / dados pessoais não publicados",
        ],
        "autenticacao": "as tools exigem header Authorization: Bearer <token>; esta doc é pública",
    }


def create_app(
    client: QdrantClient,
    collection: str,
    encoder: QueryEncoder,
    reranker=None,
    catalog=None,
) -> FastMCP:
    mcp: FastMCP = FastMCP("BaseUFF", instructions=INSTRUCTIONS)

    @mcp.tool
    def search(query: str, limit: int = 5, source: str | None = None) -> list[dict]:
        """Busca semântica no acervo aberto da UFF; retorna passagens com citação.

        Args:
            query: pergunta/termo em linguagem natural (pt-BR).
            limit: número máximo de passagens (padrão 5).
            source: filtra a fonte — "boletim" (Boletins de Serviço: portarias, nomeações,
                progressões, licenças, aposentadorias, resoluções…), "sti_kb" (manuais/tutoriais
                dos sistemas do STI, com texto das telas via OCR) ou "pesquisa" (editais PIBIC/
                bolsas). Deixe vazio para buscar em todas.

        Retorna lista de objetos {numero, source, publish_date, url, snippet, score},
        ordenados por relevância (reranqueados por cross-encoder quando disponível).
        """
        results = retrieve(
            client, collection, encoder, query, limit=limit, source=source, reranker=reranker
        )
        return [
            {
                "numero": r.numero,
                "source": r.source,
                "publish_date": r.publish_date,
                "url": r.url,
                "snippet": r.snippet,
                "score": round(r.score, 4),
            }
            for r in results
        ]

    @mcp.tool
    def info() -> dict:
        """Documentação do servidor: fontes, o que cada uma contém, cobertura ATUAL
        (contagem de trechos por fonte) e limitações. Chame isto para saber o que a base oferece."""
        return build_docs(client, collection, catalog)

    return mcp
