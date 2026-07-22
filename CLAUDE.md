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
- **skynet01** (2× RTX 3060, `cid-uff.net:22023`, `10.171.69.10`): DOIS microserviços
  `serve_encoder.py` — `baseuff-encoder` (GPU 0, `:8010`) e `baseuff-encoder-gpu1` (GPU 1, `:8011`),
  endpoints `/encode` `/rerank` `/colbert_rerank` — **e** indexação em batch (`run_batch.py`).
  O ultron balanceia entre os dois com failover (`UFF_ENCODER_URL` com vírgula; rajadas
  paralelas não empilham numa GPU só). O worker vive em `~/baseuff-worker/` (core/ e embed/).
- **skynet02 fica LIVRE** para o usuário — não usar.

Recuperação: híbrido (BGE-M3 denso+esparso, RRF) → **reranker em cascata** (ColBERT pré-seleciona →
cross-encoder BGE-reranker-v2-m3 finaliza). MRR 1.0 @ ~660ms. Vetores int8. Contextual Retrieval
(prefixo de metadados no chunk).

## Fontes (5 buscáveis) e natureza

| source | natureza | conteúdo |
|---|---|---|
| `boletim` | documento | Boletins de Serviço 1996–2026 (diário oficial interno) — fonte principal |
| `sti_kb` | tutorial | Base de Conhecimento do STI (manuais dos sistemas p/ **servidor**; OCR de telas) |
| `pesquisa` | documento | Portal da Pesquisa (editais PIBIC/bolsas) |
| `guia` | tutorial | Guia do Estudante/Comunidade (www.uff.br): diploma, matrícula, carteirinha, bolsas |
| `sbpc` | evento | 78ª Reunião Anual da SBPC na UFF (26/07–01/08/2026): programação (1 doc/atividade, `publish_date` = dia), minicursos, pôsteres (PDF), mapa do evento (transcrição curada da imagem), notícias, SBPC institucional |

`natureza` = **tutorial** (como fazer) vs **documento** (ato/registro oficial) vs **evento**
(programação/serviço de evento). É derivada de `SOURCE_KIND`/`natureza(source)` em `app.py`
**em tempo de consulta** (não fica no payload do Qdrant), então classificar uma fonte NÃO exige re-embed.

## Tools MCP

`search(query, limit, source?, date_from?, date_to?)` · `sbpc(query, limit, dia?, tipo?)` (dedicada à
78ª RA: filtros por dia do evento e tipo — mesa-redonda/conferencia/minicurso/noticia/…; resposta
estruturada com horário/local/coordenador/palestrantes, via payload `tipo`+`extra`) ·
`dossie(nome, source)` (exaustivo por pessoa; 2 níveis: **confirmados** = nome contíguo,
**provaveis** = tokens em ordem com lacunas) · `get_documento(doc_id)` · `info()`.
Doc pública em `GET /mcp` (HTML wiki) e `/mcp/docs` (JSON), sem token.

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
- **Fonte sbpc**: `ra.sbpcnet.org.br` e `reunioes2.sbpcnet.org.br` têm cadeia TLS incompleta —
  `crawl_sbpc.py` usa client `verify=False` SÓ p/ esses 2 hosts. A programação MUDA até o evento:
  `_save` compara checksum e, se um doc conhecido mudou, **purga os points no Qdrant** em QUALQUER
  status — no catálogo do ultron nada fica INDEXED (vive na cópia do worker); sem purge o
  `run_batch` pularia pelo 1º chunk e o índice ficaria velho. O filtro `tipo` da
  tool `sbpc` exige o índice de payload `tipo` (`scripts/reindex_payload.py`). Filtro `dia` usa
  `include_undated` (minicursos/pôsteres/serviço NÃO têm `publish_date` — range estrito os
  esconderia); `dia` sai `null` p/ `tipo=pagina`/`institucional` (datas velhas de WordPress);
  `max_per_doc=1` (uma atividade não repete na lista). Horário de minicurso vem do `<p>` SEM
  rótulo da página (`De 28 a 31/07/2026 - das 08h00 às 09h30`) → fragmento "Data e horário" +
  `extra.horario`/`extra.periodo` (a tool devolve `periodo`; `dia` continua null — multi-dia).
  A URL de atividade embute dia+título+hora: item movido/cancelado deixaria doc órfão com
  horário VELHO no índice — o `_gc_orfaos` purga o que sumiu da página viva (trava
  `GC_MIN_*`: parse parcial nunca deleta em massa).
- **Cascata × limit**: `CascadeReranker` marca posições além do `first_k` com scores-sentinela
  NEGATIVOS. O `retrieve` alarga o `first_k` por chamada (clone, 2× o limit) e filtra score < 0 —
  ao mexer no rerank, garanta que sentinela nunca chegue ao cliente (já vazou `-9/-10` p/ agente).
- **Apache**: `sites-enabled/` são **CÓPIAS**, não symlinks — edite lá e recarregue. Deploy exposto via
  `deploy/EXPOSICAO.md`.
- **cron tem PATH mínimo**: chame `uv` por caminho absoluto (`update.py` usa `UV = shutil.which(...)`).
  Bare `"uv"` em subprocess falha com `FileNotFoundError` sob cron.
- **Deploy do MCP**: `systemctl --user restart baseuff-mcp` (após mudar código do server) — o MCP
  é **stateless** (`stateless_http=True` no `serve.py`), restart NÃO derruba sessões de agentes.
  Encoder: `systemctl --user restart baseuff-encoder baseuff-encoder-gpu1` no skynet01 (após
  mudar `packages/embed`); reaqueça os dois (`POST /encode` dummy em `:8010` e `:8011`).
- **Idempotência do embed**: point IDs = `doc_id*10_000 + chunk_index`; `run_batch` pula doc cujo 1º
  chunk já existe no Qdrant. Reexecutar processa só o delta.

## Operação

- **Painel admin**: `/mcp/admin` (HTTP Basic; usuário `admin`, hash em `data/admin_pass.hash`
  **fora do git**). Saúde, KPIs, gráficos, tabela paginada de consultas, drill-down, gerar chave, logout.
- **Base de consultas**: `data/queries.db` (`uff_core/querylog.py`) loga cada search/dossie/get_documento
  (agente via `auth.current_agent`). Analytics: `scripts/query_stats.py [--anon]`.
- **Chaves de agente**: `./nova-chave.sh <nome>` (hot-reload de `data/mcp_tokens.txt`, sem sudo/restart).
- **Cron (ultron)**: diário 6h `boletim,pesquisa,sbpc` (sbpc diário DURANTE o evento — a programação
  muda; após 01/08/2026 mover p/ semanal); semanal dom 3h `atos,sti_kb,guia`.
- **Réplica de contingência (Modal)**: `deploy/modal/baseuff_replica.py` (mesmo serving, T4
  serverless) — **DESARMADA por padrão**: `./scripts/replica.sh armar [--pin]|desarmar|status`.
  Sync do índice no fim do `update.py` (`scripts/sync_replica.py`, best-effort). **NUNCA deixe
  nada sempre-ligado na Modal** (créditos finitos do usuário); armar só em dias críticos.
  Failover de URL: `deploy/cloudflare/` (Worker free). Ver `docs/ARQUITETURA.md`.

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
