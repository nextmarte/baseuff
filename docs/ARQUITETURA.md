# BaseUFF — Arquitetura e Operação

Servidor MCP de RAG sobre o acervo aberto da UFF. Este documento descreve a topologia
real, o fluxo de dados, as decisões de projeto e a operação.

## Topologia

Dois hosts, ligados pela rede interna da UFF:

- **ultron** (sem GPU, IP interno `10.171.69.1`, público `cid-uff.net`/`ultron.cid-uff.net`):
  - **Qdrant** (docker, `:6333`, `restart: unless-stopped`) — índice vetorial.
  - **Servidor MCP** (`scripts/serve.py`, systemd `baseuff-mcp`, `127.0.0.1:8088`).
  - **Apache** — TLS (Let's Encrypt) + `ProxyPass /mcp → 127.0.0.1:8088`.
  - **cron** — `scripts/update.py` (atualização incremental).
  - Catálogo SQLite (`data/catalog.db`) e acervo bruto (`data/raw/<fonte>/`).
  - **Não usa torch**: encode/rerank de query são chamadas HTTP ao skynet01.

- **skynet01** (2× RTX 3060, `cid-uff.net:22023`, IP interno `10.171.69.10`):
  - **`serve_encoder.py`** (systemd `baseuff-encoder`, `:8010`): microserviço FastAPI com
    `/encode` (BGE-M3 denso+esparso), `/rerank` (cross-encoder BGE-reranker-v2-m3),
    `/colbert_rerank` (late-interaction MaxSim, nativo do BGE-M3).
  - **Indexação em batch** (`run_batch.py`) sob demanda.
  - `skynet02` (`:22024`) fica **livre** para outros serviços do usuário.

```
                   ┌───────────────────────── ultron ─────────────────────────┐
 agentes de IA     │  Apache (443, TLS)                                        │
   │  HTTPS+Bearer  │    └─ /mcp ─▶ MCP FastMCP (8088, systemd)                 │
   └───────────────┼────────────────┬───────────────┬────────────────────────┤
                   │                 ▼               ▼                        │
                   │            Qdrant (6333)   encode/rerank ──HTTP──┐       │
                   └────────────────────────────────────────────────┼───────┘
                                                                     ▼
                                          ┌──────────── skynet01 (2× 3060) ────────────┐
                                          │  serve_encoder (8010): /encode /rerank      │
                                          │                        /colbert_rerank      │
                                          │  run_batch.py (indexação em batch)          │
                                          └─────────────────────────────────────────────┘
```

## Fluxo de dados (ingestão → índice)

1. **Descoberta** (`scripts/crawl.py` → conectores em `uff_ingest/connectors/`): respeita
   robots, deduplica por `(source, url)`, persiste no catálogo com status `DISCOVERED`.
   STI KB é caso à parte: `scripts/crawl_citsmart.py` (Playwright, SPA CITSmart).
2. **Download** (`scripts/download.py`): baixa `DISCOVERED` → `data/raw/<fonte>/<id>.<ext>`,
   SHA-256, status `FETCHED`. STI KB ainda passa por `enrich_sti_kb.py` (OCR das telas).
3. **Indexação** (`run_batch.py` no skynet01): para cada `FETCHED` do shard
   (`doc_id % num_shards`), faz parse híbrido (**PyMuPDF**; Docling/OCR só se escaneado,
   via `router.needs_ocr`), chunk + prefixo contextual, embed BGE-M3, upsert no Qdrant,
   status `INDEXED`. Vai de `FETCHED` **direto** a `INDEXED` (parse em memória; sem `parsed/`).

**Idempotência:** os point IDs são determinísticos (`doc_id * 10_000 + chunk_index`) e o batch
pula documentos cujo 1º chunk já existe no Qdrant. Reexecutar processa só o delta.

> Nota: o status `INDEXED` é gravado na cópia do catálogo do host GPU; a idempotência real
> vem do skip por Qdrant, não do status no catálogo do ultron.

## Fluxo de consulta (query → resposta)

1. Agente chama uma tool MCP com `Authorization: Bearer <token>`.
2. `retriever.retrieve`: encode remoto da query → duas pernas no Qdrant (denso + esparso)
   fundidas por **RRF** → over-fetch de ~24 candidatos → **reranker em cascata**
   (ColBERT pré-seleciona 8 → cross-encoder finaliza) → top-k com snippet destacado.
3. `dossie` não usa top-k: filtro **full-text** (MatchText) varre todo o acervo, pós-filtra
   pela ocorrência contígua do nome, deduplica por documento e ordena por data.
4. `get_documento` reagrupa todos os chunks de um `doc_id` na ordem original.

Índices de payload no Qdrant (`scripts/reindex_payload.py`, sem re-embed): `text` (full-text),
`publish_date` (datetime), `source`/`numero` (keyword), `doc_id` (integer).

## Segurança e exposição

- **Auth no app** (`uff_server/auth.py`): middleware ASGI exige `Bearer <token>` válido;
  tokens em `data/mcp_tokens.txt` (uma linha `agente<espaço>token`), **recarregados por mtime**
  → onboard/revogação sem reiniciar nada e sem sudo (`./nova-chave.sh`).
- **Doc pública** sem token: `GET /mcp/docs` (JSON) e `GET /mcp` de navegador (HTML wiki).
  Requisições MCP reais (POST / SSE) seguem exigindo token.
- **Apache**: `ProxyPass /mcp → 127.0.0.1:8088`, TLS do certificado `www.cid-uff.net`.
  ⚠️ **Pegadinha deste servidor:** `sites-enabled/` contém **cópias** dos vhosts (não symlinks) —
  editar o arquivo em `sites-enabled/` e recarregar. Ver `deploy/EXPOSICAO.md`.

## Operação

- **Serviços persistentes:** Qdrant (docker restart) · `baseuff-mcp` e `baseuff-encoder`
  (systemd `--user`, `loginctl enable-linger`, auto-restart + boot).
- **Atualização automática (cron no ultron):** diário 6h `boletim,pesquisa`; semanal
  domingo 3h `atos,sti_kb`. Orquestrador `scripts/update.py` (lock anti-sobreposição,
  incremental) faz descobrir→baixar→rsync→embed no skynet01→Qdrant.
- **Deploy do worker:** `rsync` de `packages/embed` para `~/baseuff-worker/embed` no skynet01;
  `systemctl --user restart baseuff-encoder`.
- **Avaliação:** `uv run python scripts/eval.py [--rerank|--colbert|--cascade] [--limit N]`
  reporta hit@1/@3/@10, MRR e latência (média/mediana/max).

## Decisões

- **Só skynet01** para o BaseUFF (embed + encode/rerank); skynet02 livre para o usuário.
- **PyMuPDF-first** no parse (~400× mais rápido que Docling); Docling/OCR só nos escaneados.
- **Encode/rerank remotos** mantêm o ultron sem torch (deploy do MCP leve e estável).
- **Cascata ColBERT→cross-encoder**: qualidade do cross-encoder (MRR 1.0) a ~0,66s/consulta,
  4,8× mais rápido que reranquear 80 candidatos direto no cross-encoder (~3,2s).
