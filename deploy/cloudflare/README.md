# Failover do BaseUFF MCP via Cloudflare Worker (workers.dev)

Endpoint público **resiliente** do BaseUFF, sem tocar no DNS do `cid-uff.net` (gerido
pelo Juan/CID no Route 53):

> **`https://mcp.baseuff.workers.dev/mcp/`**

Com a UFF saudável, o Worker repassa transparente para `https://ultron.cid-uff.net`
(latência de produção, ~0,7–0,9s por busca). Com a flag `armed=1` e a origem fora do
ar (luz/internet da UFF), cai sozinho para a réplica Modal — **mesma URL, mesmos
tokens**. Um cron de 1 min aquece a réplica assim que detecta a origem fora.

## Estado atual (já provisionado)

- Conta Cloudflare: `contato.papagaionextech@gmail.com` (account id no `wrangler.toml`).
- Worker `mcp` deployado em `mcp.baseuff.workers.dev` com cron `* * * * *`.
- KV namespace `FLAGS` (id no `wrangler.toml`); chave `armed` (padrão `0`).
- Credenciais no `.env` do repo (fora do git): `CLOUDFLARE_API_TOKEN` (template
  "Edit Cloudflare Workers"), `CF_ACCOUNT_ID`, `CF_KV_NAMESPACE_ID` — é o que o
  `scripts/replica.sh` usa para virar a flag sozinho ao armar/desarmar.

## Operação

- `./scripts/replica.sh armar` → deploya a réplica Modal **e** seta `armed=1`;
  `desarmar` → para o app Modal e seta `armed=0`. Desarmado, o Worker é um proxy
  transparente da origem (zero requisição à Modal, zero gasto).
- Flag na mão (sem replica.sh):
  `npx wrangler kv key put armed 1 --namespace-id <id> --remote` (rodar nesta pasta).
- Redeploy do Worker após editar `worker.js`/`wrangler.toml`:
  `cd deploy/cloudflare && npx wrangler deploy` (token no ambiente).

## Agentes

Trocar só a URL na config MCP (token igual):

```json
{ "mcpServers": { "baseuff": {
    "url": "https://mcp.baseuff.workers.dev/mcp/",
    "headers": { "Authorization": "Bearer <token>" }
} } }
```

Quem ficar na URL antiga (`ultron.cid-uff.net/mcp/`) continua funcionando, mas sem
failover automático (plano B manual: `https://nextmarte--baseuff-mcp.modal.run/mcp/`).

## Pegadinhas descobertas na implantação

- **`/mcp/` com barra final**: a origem devolve 307 com `Location: https://127.0.0.1:8088/mcp`
  (o `ProxyPassReverse` do Apache só reescreve `http://`, e o backend emite `https://` por
  causa do `X-Forwarded-Proto`). O Worker normaliza `/mcp/` → `/mcp` antes de encaminhar;
  sem isso, seguir o redirect dava `error 1003` (fetch para IP) na Cloudflare.
- **Timeout só até os headers** (`Promise.race`, sem `AbortSignal`): abortar o fetch
  mataria os streams SSE longos do MCP.
- **Corpo bufferizado** antes de tentar a origem: se ela cair no meio, o MESMO corpo é
  reenviado à réplica.
- Subdomínio `workers.dev` novo leva alguns minutos para emitir o certificado TLS
  (handshake failure até lá — é normal, só aguardar).

## Upgrade futuro (opcional): mesma URL de sempre

Se um dia a zona `cid-uff.net` for migrada para a Cloudflare (COMBINAR COM O JUAN —
o Route 53 pode ter registros/recursos que o scan não vê; exportar a zona antes),
basta descomentar `routes` no `wrangler.toml` e redeployar: o mesmo Worker passa a
atender `ultron.cid-uff.net/mcp*` e a URL antiga também vira resiliente. Atenção:
o registro raiz `cid-uff.net` NUNCA pode ser proxiado (nuvem laranja) — é o hostname
do SSH do skynet01 (`:22023`); só o `ultron` (HTTP) pode.

## Custo

Plano Free cobre tudo (100 mil req/dia, KV e cron inclusos). O cron não gasta créditos
da Modal com a origem saudável; desarmado, nada toca a Modal de forma alguma.
