# BaseUFF

Servidor **MCP de RAG** sobre a base de conhecimento aberta da UFF (Boletins de Serviço,
resoluções, manuais do STI/SmartUF, bases de conhecimento e Portal da Pesquisa).

O servidor é **retrieval-only**: expõe ferramentas de busca híbrida (denso + esparso, com
fusão RRF) sobre um índice vetorial e devolve passagens com citação rastreável (boletim nº,
data, órgão, página, URL). A síntese das respostas fica a cargo do cliente LLM (Claude etc.).

## Arquitetura (resumo)

```
crawl (httpx)  ->  parse (Docling/OCR)  ->  chunk + prefixo contextual  ->  embed (BGE-M3)  ->  Qdrant
   ultron              skynet02 (GPU)            (pure python)               skynet02 (GPU)     ultron
                                                                                                  |
                                                    servidor MCP (FastMCP) <---- clientes LLM ----+
                                                          ultron
```

- **ultron** (este host, sem GPU): crawler, catálogo, Qdrant e servidor MCP.
- **skynet02** (2× RTX 3060, via `cid-uff.net:22024`): parsing (Docling) e vetorização (BGE-M3), em batch.

## Estrutura (UV workspace)

| Pacote | Papel | Host |
|---|---|---|
| `packages/core` (`uff-core`) | schemas, config, catálogo | ambos |
| `packages/ingest` (`uff-ingest`) | crawler polido + conectores; extra `[parse]` = Docling | ultron (crawl) / skynet02 (parse) |
| `packages/server` (`uff-server`) | servidor MCP FastMCP | ultron |
| `packages/embed` (`uff-embed`) | worker BGE-M3 (adicionado na Fase 4) | skynet02 |

## Desenvolvimento (TDD + XP)

- TDD estrito: teste que falha **antes** do código. Commits atômicos (red → green → refactor).
- Testes offline e determinísticos (mock de HTTP, fixtures em `tests/fixtures/`), sem rede.

```bash
uv sync                 # cria o ambiente
uv run pytest           # roda a suíte
uv run ruff check .     # lint
```

## Configuração

Copie `.env.example` para `.env` e ajuste. **Nunca** versione segredos.
