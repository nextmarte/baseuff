# BaseUFF — Arquitetura e Operação

Servidor MCP de RAG sobre o acervo aberto da UFF. Este documento descreve a topologia
real, o fluxo de dados, as decisões de projeto e a operação.

## Topologia

Dois hosts, ligados pela rede interna da UFF:

- **ultron** (sem GPU, IP interno `10.171.69.1`, público `cid-uff.net`/`ultron.cid-uff.net`):
  - **Qdrant** (docker, `:6333`, `restart: unless-stopped`) — índice vetorial.
  - **Servidor MCP** (`scripts/serve.py`, systemd `baseuff-mcp`, `127.0.0.1:8088`) em modo
    **stateless** (`stateless_http=True`): restart/deploy NÃO derruba sessões de agentes.
  - **Apache** — TLS (Let's Encrypt) + `ProxyPass /mcp → 127.0.0.1:8088`.
  - **cron** — `scripts/update.py` (atualização incremental + sync da réplica Modal).
  - Catálogo SQLite (`data/catalog.db`) e acervo bruto (`data/raw/<fonte>/`).
  - **Não usa torch**: encode/rerank de query são chamadas HTTP ao skynet01, balanceadas
    entre as duas GPUs (`BalancedEncoder`/`BalancedReranker`, round-robin com failover).

- **skynet01** (2× RTX 3060, `cid-uff.net:22023`, IP interno `10.171.69.10`):
  - **DOIS microserviços `serve_encoder.py`** (FastAPI): `baseuff-encoder` (GPU 0, `:8010`) e
    `baseuff-encoder-gpu1` (GPU 1, `:8011`), cada um pinado via `CUDA_VISIBLE_DEVICES`.
    Endpoints: `/encode` (BGE-M3 denso+esparso), `/rerank` (cross-encoder BGE-reranker-v2-m3),
    `/colbert_rerank` (late-interaction MaxSim, nativo do BGE-M3).
  - **Indexação em batch** (`run_batch.py`) sob demanda.
  - `skynet02` (`:22024`) fica **livre** para outros serviços do usuário.

```
                   ┌───────────────────────── ultron ─────────────────────────┐
 agentes de IA     │  Apache (443, TLS)                                        │
   │  HTTPS+Bearer  │    └─ /mcp ─▶ MCP FastMCP (8088, systemd, stateless)      │
   └───────────────┼────────────────┬───────────────┬────────────────────────┤
                   │                 ▼               ▼                        │
                   │            Qdrant (6333)   encode/rerank ──HTTP──┐       │
                   │                            (balanceado 2 GPUs)  │       │
                   └────────────────────────────────────────────────┼───────┘
                                                                     ▼
                                          ┌──────────── skynet01 (2× 3060) ────────────┐
                                          │  serve_encoder ×2: GPU0 :8010 │ GPU1 :8011  │
                                          │    /encode /rerank /colbert_rerank          │
                                          │  run_batch.py (indexação em batch)          │
                                          └─────────────────────────────────────────────┘
```

## Fluxo de dados (ingestão → índice)

1. **Descoberta** (`scripts/crawl.py` → conectores em `uff_ingest/connectors/`): respeita
   robots, deduplica por `(source, url)`, persiste no catálogo com status `DISCOVERED`.
   Dois casos à parte, que já salvam HTML e vão direto a `FETCHED`:
   - **STI KB** (`scripts/crawl_citsmart.py`): Playwright na SPA CITSmart.
   - **guia** (`scripts/crawl_guia.py`): REST do WordPress de `www.uff.br` (httpx, sem navegador) —
     `faqs` (resposta limpa em `content.rendered`), `servico` (Carta de Serviços em Divi; salva a
     página e o trafilatura extrai no pipeline) e a página de diploma/formatura. Filtra conteúdo de
     **servidor** por taxonomia (`categoria-de-servico`/`faq_groups`; `SERVIDOR_KW` por nome).
   - **sbpc** (`scripts/crawl_sbpc.py`): 8 coletores — programação científica de
     `reunioes2.sbpcnet.org.br` (1 doc **por atividade**, fragmento HTML sintetizado com dia/
     horário/local/pessoas; `publish_date` = dia da atividade), minicursos, REST dos WordPress
     `ra.sbpcnet.org.br/78RA` e `sbpc.uff.br`, mapa do evento (o site publica só a IMAGEM;
     salvamos transcrição curada da legenda — blocos, espaços e serviços — com purge explícito
     dos points ao mudar), páginas institucionais do portal SBPC, notícias
     (links externos da listagem da 78RA → Jornal da Ciência/www.uff.br) e PDFs (pôsteres,
     programações temáticas). Hosts `*.sbpcnet.org.br` da RA têm cadeia TLS incompleta → client
     `verify=False` só para eles. Como a programação muda, `_save` compara **checksum** e, se um
     doc `INDEXED` mudou, **purga os points no Qdrant** e rebaixa a `FETCHED` (senão o `run_batch`
     pularia o doc pelo 1º chunk). O payload ganha `title`/`orgao`/`tipo`/`extra` — a tool `sbpc`
     filtra por `tipo` (índice keyword via `reindex_payload.py`) e devolve resposta estruturada.
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
   (ColBERT pré-seleciona `first_k` → cross-encoder finaliza) → **diversificação por
   documento** (`_diversify`, máx. 2 trechos do mesmo doc; a tool `sbpc` usa 1 — uma
   atividade não repete na lista) → top-k com snippet destacado. Pegadinhas cobertas:
   com `limit > first_k` o `first_k` é alargado por chamada (clone, 2× o limit) e scores
   sentinela da cascata (negativos) **nunca** chegam ao cliente; na tool `sbpc`, filtro de
   `dia` usa `include_undated` (minicursos/pôsteres/serviço são multi-dia, sem
   `publish_date` — um range estrito os esconderia) e `dia` sai `null` para
   `tipo=pagina`/`institucional` (datas herdadas do WordPress de edições antigas).
3. `dossie` não usa top-k: filtro **full-text** (MatchText) varre todo o acervo e classifica cada
   documento em **dois níveis** — `confirmados` (nome **contíguo** no texto) e `provaveis` (mesmos
   tokens em ordem com até N palavras no meio, recupera nomes compostos); dedup por documento,
   cronológico. Os prováveis podem conter homônimos → o cliente verifica.
4. `get_documento` reagrupa todos os chunks de um `doc_id` na ordem original.

Toda saída de tool passa por **`mask_cpf`** (`uff_server/pii.py`): CPF é anonimizado na entrega
(`***.***.***-**`), sem tocar em nº de processo/SIAPE; o índice Qdrant permanece **cru**. Cada
resultado carrega `natureza` (`tutorial`/`documento`), derivada de `SOURCE_KIND` em tempo de consulta
(não fica no payload → classificar uma fonte não exige re-embed).

Índices de payload no Qdrant (`scripts/reindex_payload.py`, sem re-embed): `text` (full-text),
`publish_date` (datetime), `source`/`numero` (keyword), `doc_id` (integer).

## Segurança e exposição

- **Auth no app** (`uff_server/auth.py`): middleware ASGI exige `Bearer <token>` válido;
  tokens em `data/mcp_tokens.txt` (uma linha `agente<espaço>token`), **recarregados por mtime**
  → onboard/revogação sem reiniciar nada e sem sudo (`./nova-chave.sh`).
- **Doc pública** sem token: `GET /mcp/docs` (JSON) e `GET /mcp` de navegador (HTML wiki).
  Requisições MCP reais (POST / SSE) seguem exigindo token.
- **Painel de administração** (`uff_server/admin.py`, `GET /mcp/admin` + `/mcp/admin/api`): **HTTP
  Basic** próprio (usuário `admin`, hash sha256 em `data/admin_pass.hash`, fora do git). Mostra saúde
  (Qdrant/encoder/acervo), KPIs, gráficos e a tabela paginada de consultas (CPF mascarado); permite
  drill-down (clicar numa consulta a re-executa), emitir chave de agente e logout. `/mcp/admin/logout`
  é público (landing após limpar o Basic Auth).
- **Apache**: `ProxyPass /mcp → 127.0.0.1:8088`, TLS do certificado `www.cid-uff.net`.
  ⚠️ **Pegadinha deste servidor:** `sites-enabled/` contém **cópias** dos vhosts (não symlinks) —
  editar o arquivo em `sites-enabled/` e recarregar. Ver `deploy/EXPOSICAO.md`.

## Base de consultas (gestão de qualidade + pesquisa)

Cada chamada de tool de busca (`search`/`dossie`/`get_documento`) é registrada em
`data/queries.db` (SQLite/WAL, `uff_core/querylog.py`): timestamp, **agente** (resolvido do
token via `auth.current_agent`, propagado por contextvar), tool, query, filtros, nº de
resultados, latência e o topo dos resultados. `info()` (público) não é registrado.

- **Thread-safe:** grava com conexão nova por escrita (as tools rodam em worker threads).
- **Analytics:** `uv run python scripts/query_stats.py` — uso por tool/agente/fonte/dia,
  latência p50/p95, top queries e **lacunas** (dossiê/doc sem resultado + buscas cujo melhor
  score de reranker ficou baixo → sinal de conteúdo faltante). `--anon` anonimiza agentes.
- **Privacidade:** a base fica só no ultron (ambiente privado); para publicação/paper,
  usar apenas agregados anonimizados (queries podem conter nomes de pessoas).

## Operação

- **Serviços persistentes:** Qdrant (docker restart) · `baseuff-mcp` e `baseuff-encoder`
  (systemd `--user`, `loginctl enable-linger`, auto-restart + boot).
- **Atualização automática (cron no ultron):** diário 6h `boletim,pesquisa,sbpc` (sbpc diário
  enquanto durar o evento — após 01/08/2026 mover para a semanal); semanal
  domingo 3h `atos,sti_kb,guia`. Orquestrador `scripts/update.py` (lock anti-sobreposição,
  incremental) faz descobrir→baixar→rsync→embed no skynet01→Qdrant.
  ⚠️ **cron tem PATH mínimo**: o `update.py` resolve o `uv` por caminho absoluto
  (`shutil.which("uv")` + fallback `~/.local/bin/uv`); chamar `"uv"` puro em subprocess falha com
  `FileNotFoundError` sob cron. Ao adicionar uma fonte, sincronize `uff_core` para o worker
  (`~/baseuff-worker/core`) antes do embed, senão `run_batch` quebra em `Source(<nova>)`.
- **Deploy do worker:** `rsync` de `packages/embed` para `~/baseuff-worker/embed` no skynet01;
  `systemctl --user restart baseuff-encoder baseuff-encoder-gpu1` (um processo por GPU:
  `:8010` na GPU 0, `:8011` na GPU 1; o ultron balanceia com failover via `UFF_ENCODER_URL`
  com URLs separadas por vírgula — rajada de 8 consultas paralelas caiu de 9,7s p/ 4,1s máx).
- **Avaliação:** `uv run python scripts/eval.py [--rerank|--colbert|--cascade] [--limit N]`
  reporta hit@1/@3/@10, MRR e latência (média/mediana/max).

## Réplica de contingência (Modal) — armável sob demanda

Se a UFF perde luz/internet, `ultron.cid-uff.net/mcp` some da internet e **nenhum hardening
interno resolve**. A saída é uma réplica fora da UFF com o MESMO código de serving, na Modal
(serverless, scale-to-zero), definida em `deploy/modal/baseuff_replica.py`:

- **Função `mcp` (CPU, 2 vCPU/6GB)**: Qdrant **1.18.2** (mesma versão do ultron) restaurado do
  snapshot no Volume `baseuff-data` + FastMCP montado igual ao `scripts/serve.py` (também
  stateless). `max_containers=1` por economia — com o MCP stateless não é mais exigência
  de sessão; pode subir se precisar de mais vazão num outage.
- **Classe `Encoder` (GPU T4)**: BGE-M3 + ColBERT + cross-encoder de `packages/embed`, modelos
  **baked na imagem** (não depende do HuggingFace em runtime). **Sem URL pública** — o server
  chama as funções GPU pela própria Modal (autenticado); ninguém de fora drena créditos por aí.
  Qualidade idêntica à produção (mesma cascata/índice); latência quente medida ~3s/consulta
  (3 chamadas GPU por busca com overhead de ~0,3–0,5s cada no function call da Modal) e
  cold start medido de ~49s (restauração do snapshot de 4,4GB + boot).
- **Sync**: `scripts/sync_replica.py` (chamado no fim do `update.py`, **best-effort/não-fatal**;
  sem CLI da modal instalada ele pula com aviso) empurra snapshot do Qdrant + `catalog.db` +
  `mcp_tokens.txt` + `admin_pass.hash` + `manifest.json` para o Volume. Upload não usa compute.
- **Armar/desarmar** (`scripts/replica.sh`): DESARMADA por padrão (`modal app stop` — nada sobe
  nem cobra; trava dura). `armar [--pin]` = `modal deploy` (+pin: 1 container quente ~US$1/h,
  só p/ dias críticos). Armada com a UFF saudável custa ~US$0; outage real ~US$2–5/dia.
- **Failover** (`deploy/cloudflare/`, IMPLANTADO): Worker free em
  `https://mcp.baseuff.workers.dev/mcp/` (URL resiliente p/ agentes) tenta a origem UFF
  (timeout 5s até os headers) e cai p/ `https://nextmarte--baseuff-mcp.modal.run`; cron de
  1min aquece a réplica quando detecta a origem fora. O DNS do `cid-uff.net` (Route 53, gerido
  pelo Juan) NÃO foi tocado — migrar a zona p/ a Cloudflare é upgrade futuro opcional (aí a
  URL antiga também vira resiliente). Janela percebida de failover: ~1–2min.

## Decisões

- **Só skynet01** para o BaseUFF (embed + encode/rerank); skynet02 livre para o usuário.
- **PyMuPDF-first** no parse (~400× mais rápido que Docling); Docling/OCR só nos escaneados.
- **Encode/rerank remotos** mantêm o ultron sem torch (deploy do MCP leve e estável).
- **Cascata ColBERT→cross-encoder**: qualidade do cross-encoder (MRR 1.0) a ~0,66s/consulta,
  4,8× mais rápido que reranquear 80 candidatos direto no cross-encoder (~3,2s).
- **Réplica Modal armável, nunca sempre-ligada**: os créditos (US$250) não podem ser drenados
  por standby — app parado por padrão + spending cap no workspace; armar só em dias de alta
  demanda (ex.: semana da SBPC 26/07–01/08/2026).
