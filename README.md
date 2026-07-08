# BaseUFF

Servidor **MCP de RAG** sobre o acervo **aberto** da Universidade Federal Fluminense.
Agentes de IA consultam, em linguagem natural, ~392 mil trechos indexados com citação
rastreável (nº do documento, data, URL). Em produção em `https://ultron.cid-uff.net/mcp`.

**Acervo indexado (buscável):** três fontes —
- **boletim** — Boletins de Serviço (diário oficial interno, **1996–2026**): portarias, nomeações,
  progressões/promoções, licenças, aposentadorias, diárias, resoluções dos conselhos, editais.
- **sti_kb** — Base de Conhecimento do STI: manuais/tutoriais dos sistemas, com o **texto das
  telas** extraído por OCR.
- **pesquisa** — Portal da Pesquisa: editais e notícias (PIBIC, bolsas de iniciação científica).

> `resolucao` (Atos Normativos) entra como índice de metadados apontando para o Boletim, não
> como conteúdo buscável. Não há acesso a SIAPE/sistemas internos/dados financeiros.

## Recuperação (estado da arte para o tamanho da base)

Busca **híbrida** (denso BGE-M3 + esparso/lexical, fusão RRF) → **reranker em cascata**:
**ColBERT** (late-interaction, rápido) pré-seleciona os candidatos e o **cross-encoder**
(BGE-reranker-v2-m3) finaliza o topo. Medido no harness `scripts/eval.py`:

| Reranker | MRR | Latência/consulta |
|---|---|---|
| híbrido (baseline) | 0.877 | ~70 ms |
| cross-encoder | 1.000 | ~930 ms |
| ColBERT | 0.927 | ~380 ms |
| **cascata (padrão)** | **1.000** | **~660 ms** |

Vetores densos com **quantização int8** (RAM ~4× menor, rescoring preserva a acurácia).

## Ferramentas MCP (suite agêntica)

- `search(query, limit=5, source=None, date_from=None, date_to=None)` — busca por **tema**
  (híbrido + reranker), com filtros de fonte e período; retorna passagens com citação.
- `dossie(nome, source="boletim")` — levantamento **exaustivo** de uma pessoa/entidade
  (todos os atos, não top-k; dedup por documento; cronológico). Fecha a limitação do top-k.
- `get_documento(doc_id)` — reconstrói um ato/documento **inteiro** para contexto pleno.
- `info()` — documentação + dimensão do acervo ao vivo.

**Documentação pública** em `GET /mcp` (navegador → página wiki HTML; agente → JSON em
`/mcp/docs`), **sem token**. As ferramentas exigem `Authorization: Bearer <token>`
(uma chave por agente em `data/mcp_tokens.txt`; gerencie com `./nova-chave.sh`).

## Arquitetura

```
crawl (httpx/Playwright) → parse híbrido (PyMuPDF; Docling/OCR só p/ escaneados)
   ultron                  → chunk + prefixo contextual → embed BGE-M3 → Qdrant
                                                            skynet01 (GPU)   ultron
                                                                               │
   agentes de IA ──HTTPS+Bearer──▶ Apache /mcp ──▶ MCP (FastMCP) ─▶ Qdrant + encode/rerank
                                     ultron              ultron          ultron / skynet01
```

- **ultron** (sem GPU): crawler, catálogo, Qdrant (docker), servidor MCP (systemd), Apache
  (TLS + proxy `/mcp`), cron de atualização. O MCP não usa torch — encode/rerank são remotos.
- **skynet01** (2× RTX 3060, `cid-uff.net:22023`): indexação em batch **e** microserviço
  online `serve_encoder.py` (`/encode`, `/rerank`, `/colbert_rerank`), systemd. skynet02 fica livre.

Detalhes de topologia, fluxo de dados e operação em [`docs/ARQUITETURA.md`](docs/ARQUITETURA.md).

## Estrutura (UV workspace)

| Pacote | Papel | Host |
|---|---|---|
| `packages/core` (`uff-core`) | schemas, config, catálogo (SQLite), chunking | ambos |
| `packages/ingest` (`uff-ingest`) | crawler polido + conectores + OCR de telas | ultron |
| `packages/server` (`uff-server`) | MCP FastMCP: tools, retriever, auth, encoder/reranker remotos | ultron |
| `packages/embed` (`uff-embed`) | BGE-M3 + reranker + parsing (torch/GPU) | skynet01 |

`packages/embed` **não** é membro do workspace (deps de GPU); seus testes rodam com
`PYTHONPATH=packages/embed uv run --with pymupdf pytest packages/embed/tests`.

## Scripts

| Script | Função |
|---|---|
| `scripts/crawl.py` / `download.py` | descoberta e download por fonte |
| `scripts/crawl_citsmart.py` | crawler Playwright do STI KB (CITSmart) |
| `scripts/enrich_sti_kb.py` | OCR (RapidOCR) das telas dos tutoriais |
| `scripts/update.py` | orquestrador **incremental** (cron): descobrir→baixar→embed no skynet01 |
| `scripts/serve.py` | entrypoint do servidor MCP (stdio ou HTTP) |
| `scripts/reindex_payload.py` | índices de payload no Qdrant (full-text, datetime, keyword) |
| `scripts/quantize.py` | quantização int8 da coleção |
| `scripts/eval.py` | harness de avaliação (hit@k, MRR, latência) — `--rerank/--colbert/--cascade` |
| `nova-chave.sh` | gerar/listar/revogar chaves de agente (hot-reload, sem sudo) |

## Desenvolvimento (TDD)

```bash
uv sync              # ambiente do workspace (core/ingest/server)
uv run pytest        # suíte offline e determinística (mock de HTTP, fixtures)
uv run ruff check .  # lint
```

Copie `.env.example` para `.env` e ajuste. **Nunca** versione segredos.
