# CLAUDE.md

Guia para agentes (Claude Code) trabalharem neste repositório. Leia antes de editar.

## O que é

**BaseUFF** — servidor **MCP de RAG** (retrieval-only) sobre o acervo **aberto** da UFF.
Agentes de IA buscam ~509 mil trechos com citação rastreável. Produção em
`https://ultron.cid-uff.net/mcp`. Workspace **UV** (Python 3.12), FastMCP, Qdrant, BGE-M3.

## Comandos

```bash
uv sync                       # ambiente do workspace (core/ingest/server)
uv run pytest                 # suíte offline determinística (108 testes; mock de HTTP/fixtures)
uv run pytest packages/server/tests/test_app.py::test_search_tool_end_to_end   # 1 teste
uv run ruff check .           # lint (obrigatório antes de commit)
uv run ruff format .          # formatação
uv run python scripts/eval.py --cascade   # avaliação (hit@k, MRR, latência)
```

- **`packages/embed` NÃO é membro do workspace** (deps de GPU/torch). Rode seus testes com
  `PYTHONPATH=packages/embed uv run --with pymupdf pytest packages/embed/tests`.
- `packages/embed` só é importável no **skynet01** (host GPU); no ultron ele não instala.

## Arquitetura (2 hosts)

- **ultron** (sem GPU, `10.171.69.1`, público `ultron.cid-uff.net`): crawler, catálogo SQLite,
  Qdrant (docker `:6333`), MCP (`scripts/serve.py`, systemd `--user` `baseuff-mcp`, `127.0.0.1:8088`),
  Apache (TLS + `ProxyPass /mcp`), cron. **Não usa torch** — encode/rerank são HTTP ao skynet01.
- **skynet01** (2× RTX 3060, `cid-uff.net:22023`, `10.171.69.10`): microserviço
  `serve_encoder.py` (systemd `baseuff-encoder`, `:8010`: `/encode` `/rerank` `/colbert_rerank`)
  **e** indexação em batch (`run_batch.py`). O worker vive em `~/baseuff-worker/` (core/ e embed/).
- **skynet02 fica LIVRE** para o usuário — não usar.

Recuperação: híbrido (BGE-M3 denso+esparso, RRF) → **reranker em cascata** (ColBERT pré-seleciona →
cross-encoder BGE-reranker-v2-m3 finaliza). MRR 1.0 @ ~660ms. Vetores int8. Contextual Retrieval
(prefixo de metadados no chunk).

## Fontes (4 buscáveis) e natureza

| source | natureza | conteúdo |
|---|---|---|
| `boletim` | documento | Boletins de Serviço 1996–2026 (diário oficial interno) — fonte principal |
| `sti_kb` | tutorial | Base de Conhecimento do STI (manuais dos sistemas p/ **servidor**; OCR de telas) |
| `pesquisa` | documento | Portal da Pesquisa (editais PIBIC/bolsas) |
| `guia` | tutorial | Guia do Estudante/Comunidade (www.uff.br): diploma, matrícula, carteirinha, bolsas |

`natureza` = **tutorial** (como fazer) vs **documento** (ato/registro oficial). É derivada de
`SOURCE_KIND`/`natureza(source)` em `app.py` **em tempo de consulta** (não fica no payload do Qdrant),
então classificar uma fonte NÃO exige re-embed.

## Tools MCP

`search(query, limit, source?, date_from?, date_to?)` · `dossie(nome, source)` (exaustivo por pessoa;
2 níveis: **confirmados** = nome contíguo, **provaveis** = tokens em ordem com lacunas) ·
`get_documento(doc_id)` · `info()`. Doc pública em `GET /mcp` (HTML wiki) e `/mcp/docs` (JSON), sem token.

## Regras e pegadinhas (IMPORTANTE)

- **TDD**: escreva/atualize teste junto. Rode `pytest` + `ruff check` antes de qualquer commit.
- **PII/LGPD**: `mask_cpf` (`uff_server/pii.py`) mascara CPF **só na saída** das tools; o índice Qdrant
  fica **cru** (decisão do usuário). Ao mexer em search/dossie/get_documento, mantenha o mask na saída.
- **Ambiente privado**: servidor seguro dentro da rede da UFF. **Pode colar CPF/credenciais**; não fique
  pedindo confirmação de PII.
- **Adicionar uma fonte nova** (ex.: como foi feito o `guia`): (1) `Source.X` em `core/uff_core/schemas.py`;
  (2) rótulo em `core/uff_core/chunking.py` `_SOURCE_LABEL`; (3) `SOURCES` + `SOURCE_KIND` em
  `server/uff_server/app.py`; (4) crawler `scripts/crawl_X.py` que salva `data/raw/X/{id}.html` + catálogo
  (status FETCHED); (5) **sincronizar `uff_core` para o worker** (`rsync packages/core/uff_core/ →
  ~/baseuff-worker/core/uff_core/`) senão `run_batch` quebra com `ValueError('X')`; (6) embed:
  `run_batch.py --sources X` no skynet01 **com `CUDA_VISIBLE_DEVICES=1`** (single-GPU); (7) ramo em
  `scripts/update.py ingest()` + incluir no cron.
- **Embed single-GPU**: use `CUDA_VISIBLE_DEVICES=1` no `run_batch`. O pool multi-GPU do FlagEmbedding
  **pendura** em lotes grandes; se travar, `pkill -f baseuff-worker/embed/[.]venv`.
- **Apache**: `sites-enabled/` são **CÓPIAS**, não symlinks — edite lá e recarregue. Deploy exposto via
  `deploy/EXPOSICAO.md`.
- **cron tem PATH mínimo**: chame `uv` por caminho absoluto (`update.py` usa `UV = shutil.which(...)`).
  Bare `"uv"` em subprocess falha com `FileNotFoundError` sob cron.
- **Deploy do MCP**: `systemctl --user restart baseuff-mcp` (após mudar código do server). Encoder:
  `systemctl --user restart baseuff-encoder` no skynet01 (após mudar `packages/embed`).
- **Idempotência do embed**: point IDs = `doc_id*10_000 + chunk_index`; `run_batch` pula doc cujo 1º
  chunk já existe no Qdrant. Reexecutar processa só o delta.

## Operação

- **Painel admin**: `/mcp/admin` (HTTP Basic; usuário `admin`, hash em `data/admin_pass.hash`
  **fora do git**). Saúde, KPIs, gráficos, tabela paginada de consultas, drill-down, gerar chave, logout.
- **Base de consultas**: `data/queries.db` (`uff_core/querylog.py`) loga cada search/dossie/get_documento
  (agente via `auth.current_agent`). Analytics: `scripts/query_stats.py [--anon]`.
- **Chaves de agente**: `./nova-chave.sh <nome>` (hot-reload de `data/mcp_tokens.txt`, sem sudo/restart).
- **Cron (ultron)**: diário 6h `boletim,pesquisa`; semanal dom 3h `atos,sti_kb,guia`.

## Onde as coisas vivem

| Pacote | Papel |
|---|---|
| `packages/core` (`uff-core`) | schemas, config, catálogo SQLite, chunking, **querylog** |
| `packages/ingest` (`uff-ingest`) | crawler httpx+robots, conectores, download, OCR de telas |
| `packages/server` (`uff-server`) | MCP: `app.py` (tools/docs/natureza), `retriever.py`, `auth.py`, `admin.py`, `pii.py`, encoder/reranker remotos |
| `packages/embed` (`uff-embed`) | BGE-M3, reranker, parsing (torch/GPU; só skynet01) |

Segredos: nunca versione. `.env` (copie de `.env.example`), `data/*.hash`, `data/mcp_tokens.txt` e todo
`data/` ficam fora do git. Mensagens de commit em português, atômicas.

Detalhes completos: [`README.md`](README.md) e [`docs/ARQUITETURA.md`](docs/ARQUITETURA.md).
