# BaseUFF

Servidor **MCP de RAG** sobre o acervo **aberto** da Universidade Federal Fluminense.
Agentes de IA consultam, em linguagem natural, ~509 mil trechos indexados com citação
rastreável (nº do documento, data, URL). Em produção em `https://mcp.baseuff.workers.dev/mcp`
(URL resiliente, com failover automático p/ réplica Modal; a direta
`https://ultron.cid-uff.net/mcp` some se a UFF perder luz/internet).

**Acervo indexado (buscável):** cinco fontes, cada uma com uma **natureza** — `documento`
(ato/registro oficial já publicado), `tutorial` (como fazer, passo a passo) ou `evento`
(programação/serviço de evento):

- **boletim** *(documento)* — Boletins de Serviço (diário oficial interno, **1996–2026**): portarias,
  nomeações, progressões/promoções, licenças, aposentadorias, diárias, resoluções, editais.
- **sti_kb** *(tutorial)* — Base de Conhecimento do STI: manuais dos sistemas (para **servidores**),
  com o **texto das telas** extraído por OCR.
- **pesquisa** *(documento)* — Portal da Pesquisa: editais e notícias (PIBIC, bolsas de IC).
- **guia** *(tutorial)* — Guia do Estudante e da Comunidade (www.uff.br): como emitir/obter **2ª via de
  diploma**, colação de grau, matrícula, carteirinha, bolsas/auxílios, estágios, mobilidade — a Carta
  de Serviços e o FAQ do estudante (conteúdo de servidor é filtrado por taxonomia).
- **sbpc** *(evento)* — **78ª Reunião Anual da SBPC na UFF** (Campus Gragoatá, Niterói,
  **26/07–01/08/2026**): programação científica completa (1 documento por atividade, com dia,
  horário, local, coordenador e palestrantes), minicursos/webminicursos (ementa, público-alvo),
  caderno de **pôsteres** (trabalhos com autores), trilhas temáticas (Gênero, Afro e Indígena,
  Jovem, Cultural), serviço do evento (inscrição, hospedagem), notícias e a SBPC institucional
  (história, estatuto, diretoria).

Cada resultado traz o campo `natureza`, e a doc pública explica a distinção — para o agente saber se
está entregando um **procedimento** (tutorial) ou um **ato oficial** (documento).

> `resolucao` (Atos Normativos) entra como índice de metadados apontando para o Boletim, não
> como conteúdo buscável. Não há acesso a SIAPE/sistemas internos/dados financeiros.
> **CPF** presente no acervo é **anonimizado na saída** das tools (o índice permanece cru).

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
  (híbrido + reranker), com filtros de fonte e período; retorna passagens com citação e `natureza`.
  Diversifica por documento (máx. 2 trechos do mesmo doc no top-k).
- `sbpc(query, limit=5, dia=None, tipo=None)` — busca **dedicada à 78ª Reunião Anual da SBPC**:
  filtros por **dia do evento** (`"2026-07-29"` ou `"29/07"`) e **tipo** (mesa-redonda, conferencia,
  minicurso, noticia, institucional…); resposta estruturada com dia, horário, local, modalidade,
  coordenador, palestrantes e trilha, pronta para montar roteiro de participante.
- `dossie(nome, source="boletim")` — levantamento **exaustivo** de uma pessoa/entidade (todos os
  atos, não top-k; dedup por documento; cronológico), em **dois níveis**: `confirmados` (nome
  contíguo, alta precisão) e `provaveis` (mesmos tokens em ordem com partes no meio — recupera nomes
  compostos, ex.: "Mariana Marinho Peixoto" acha "Mariana Marinho da Costa Lima Peixoto").
- `get_documento(doc_id)` — reconstrói um ato/documento **inteiro** para contexto pleno.
- `info()` — documentação + dimensão do acervo ao vivo (por fonte, com natureza).

**Documentação pública** em `GET /mcp` (navegador → página wiki HTML; agente → JSON em
`/mcp/docs`), **sem token**. As ferramentas exigem `Authorization: Bearer <token>`
(uma chave por agente em `data/mcp_tokens.txt`; gerencie com `./nova-chave.sh`).

**Painel de administração** em `/mcp/admin` (HTTP Basic próprio): saúde do serviço, KPIs, gráficos,
tabela paginada de consultas com drill-down (clicar re-executa a consulta), emissão de chave e logout.

## Arquitetura

```
crawl (httpx/Playwright) → parse híbrido (PyMuPDF; Docling/OCR só p/ escaneados)
   ultron                  → chunk + prefixo contextual → embed BGE-M3 → Qdrant
                                                            skynet01 (GPU)   ultron
                                                                               │
   agentes de IA ──HTTPS+Bearer──▶ Apache /mcp ──▶ MCP (FastMCP) ─▶ Qdrant + encode/rerank
                                     ultron              ultron          ultron / skynet01
```

- **ultron** (sem GPU): crawler, catálogo, Qdrant (docker), servidor MCP (systemd, HTTP
  **stateless** — deploy não derruba agentes), Apache (TLS + proxy `/mcp`), cron de atualização.
  O MCP não usa torch — encode/rerank são remotos, balanceados entre as GPUs com failover.
- **skynet01** (2× RTX 3060, `cid-uff.net:22023`): indexação em batch **e** DOIS microserviços
  `serve_encoder.py` (GPU 0 `:8010`, GPU 1 `:8011`; `/encode`, `/rerank`, `/colbert_rerank`),
  systemd. skynet02 fica livre.
- **Contingência** (queda de luz/internet na UFF): réplica **armável** na Modal (mesmo código,
  T4 serverless, gasto zero desarmada) + failover na URL resiliente
  `https://mcp.baseuff.workers.dev/mcp/` (Cloudflare Worker). `./scripts/replica.sh armar|desarmar`.

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
| `scripts/crawl_guia.py` | crawler (REST WordPress) do Guia do Estudante — fonte `guia` |
| `scripts/enrich_sti_kb.py` | OCR (RapidOCR) das telas dos tutoriais |
| `scripts/update.py` | orquestrador **incremental** (cron): descobrir→baixar→embed no skynet01→sync da réplica |
| `scripts/serve.py` | entrypoint do servidor MCP (stdio ou HTTP stateless) |
| `scripts/sync_replica.py` | empurra snapshot/catálogo/tokens p/ o Volume da réplica Modal (best-effort) |
| `scripts/replica.sh` | arma/desarma a réplica de contingência (`armar [--pin]`, `desarmar`, `status`) |
| `scripts/reindex_payload.py` | índices de payload no Qdrant (full-text, datetime, keyword) |
| `scripts/quantize.py` | quantização int8 da coleção |
| `scripts/query_stats.py` | analytics da base de consultas (uso, latência, lacunas) — `--anon` |
| `scripts/eval.py` | harness de avaliação (hit@k, MRR, latência) — `--rerank/--colbert/--cascade` |
| `nova-chave.sh` | gerar/listar/revogar chaves de agente (hot-reload, sem sudo) |

## Como rodar o projeto

O serving são **três processos**: o **Qdrant** (índice), o **encoder** (BGE-M3 + rerankers,
em GPU) e o **servidor MCP**. Na UFF eles vivem em 2 hosts (ultron sem GPU + skynet01 com
GPU), mas num box único com GPU — por exemplo uma **EC2 `g4dn.xlarge` (T4 16 GB)**, a mesma
GPU que a réplica Modal usa — roda tudo junto.

**Pré-requisitos:** Python 3.12 + [uv](https://docs.astral.sh/uv/), Docker (Qdrant) e uma GPU
NVIDIA para o encoder (sem GPU roda em CPU, porém lento). Copie `.env.example` para `.env`.

### 1. Índice vetorial (Qdrant)

```bash
docker run -d --name qdrant -p 6333:6333 \
  -v "$PWD/qdrant_storage:/qdrant/storage" \
  -v "$PWD/data/replica_sync:/snapshots" \
  qdrant/qdrant:v1.18.2
```

Restaure a coleção a partir do snapshot que o `scripts/sync_replica.py` gera
(`data/replica_sync/uff_chunks.snapshot`) — **não precisa re-indexar**:

```bash
curl -X PUT 'http://localhost:6333/collections/uff_chunks/snapshots/recover' \
  -H 'content-type: application/json' \
  -d '{"location":"file:///snapshots/uff_chunks.snapshot"}'
```

> Sem snapshot? Reconstrua o índice do zero com `packages/embed/run_batch.py` num host com
> GPU (parse → chunk → embed → upsert). Ver [`docs/ARQUITETURA.md`](docs/ARQUITETURA.md).

### 2. Encoder + rerankers (host com GPU)

```bash
cd packages/embed
uv venv && uv pip install -e .          # torch/GPU isolado (não é membro do workspace)
CUDA_VISIBLE_DEVICES=0 uv run uvicorn serve_encoder:app --host 0.0.0.0 --port 8010
```

Expõe `/encode`, `/rerank`, `/colbert_rerank`. Verifique: `curl localhost:8010/healthz`.

### 3. Servidor MCP

No `.env`, aponte para os dois serviços acima:

```dotenv
UFF_QDRANT_URL=http://localhost:6333
UFF_ENCODER_URL=http://127.0.0.1:8010   # várias URLs por vírgula = balanceia entre GPUs
```

```bash
uv sync                                        # ambiente do workspace (core/ingest/server)
uv run python scripts/serve.py --http 8088     # HTTP (clientes remotos, auth Bearer)
# ou, para Claude Code/Desktop local via stdio (sem auth):
uv run python scripts/serve.py
```

Teste: `curl http://localhost:8088/mcp/docs` (doc pública, sem token). As tools exigem
`Authorization: Bearer <token>` — gere um com `./nova-chave.sh <agente>`.

### Num box único com GPU (EC2 / self-hosted)

Os três processos co-locados numa máquina só. Diferenças vs. a topologia UFF de 2 hosts:

- `UFF_ENCODER_URL=http://127.0.0.1:8010` — encoder é local, sem hop de rede.
- **Uma GPU só** → um `serve_encoder` (dispensa o balanceamento `:8010,:8011`).
- **Dados** entram pelo snapshot (passo 1), não por re-crawl.
- Atrás de um proxy TLS (Caddy/ALB/Apache), **reescreva `Host: 127.0.0.1`**: o transporte
  streamable-http do MCP rejeita Host que não seja localhost (senão devolve **HTTP 421**).

`deploy/modal/baseuff_replica.py` é uma referência funcional desse serving co-locado (mesmo
código, T4 serverless) — vale ler como especificação.

### Sem GPU numa instância gratuita (t3.micro)

O free tier é uma `t3.micro`/`t2.micro`: **1 vCPU, 1 GB de RAM, sem GPU**. Dá para rodar o
serviço nela, **mas com uma ressalva dura**: o BGE-M3 + o reranker **não carregam em 1 GB**
(o modelo sozinho passa de 1 GB, com ou sem GPU). A inferência precisa vir de fora.

O que **cabe** na micro é o papel que o *ultron* já faz — o **MCP é torch-free de propósito**
(deps: FastMCP + qdrant-client, nenhum torch), então ele serve de front e **delega** o
encode/rerank a um encoder remoto com GPU:

```dotenv
# um host com GPU rodando serve_encoder.py: skynet01, um endpoint exposto da réplica
# Modal, ou qualquer outro. O MCP na micro não faz inferência.
UFF_ENCODER_URL=http://<host-com-gpu>:8010
UFF_QDRANT_URL=http://localhost:6333
```

O Qdrant com 511k points é apertado em 1 GB: rode-o em **modo disco** (vetores/HNSW `on_disk`,
sem `always_ram` — mais lento, mas cabe) ou aponte `UFF_QDRANT_URL` para um Qdrant remoto.
Se a micro engasgar, uma `t3.small` (2 GB, ~US$15/mês) resolve com folga.

**E rodar tudo em CPU, sem GPU nenhuma?** É possível no código (`Reranker(device="cpu")`,
`Bge(use_fp16=False)`), mas aí o modelo exige **~8 GB de RAM (t3.large, já fora do free tier)**
e a latência sobe para **segundos por consulta**. Ou seja: **"grátis" e "sem GPU e sem
delegar" não coexistem** — 1 GB não segura os modelos. Ou você é grátis delegando a GPU, ou
roda sem GPU num box maior pago.

## Desenvolvimento (TDD)

```bash
uv sync              # ambiente do workspace (core/ingest/server)
uv run pytest        # suíte offline e determinística (mock de HTTP, fixtures)
uv run ruff check .  # lint
```

Copie `.env.example` para `.env` e ajuste. **Nunca** versione segredos.
