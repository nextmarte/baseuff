# Expor o servidor MCP na internet (sem AWS, sem DNS novo)

O `ultron.cid-uff.net` já resolve para esta máquina e já tem TLS. Expomos o MCP num
**caminho** desse subdomínio: `https://ultron.cid-uff.net/mcp/`. Sem subdomínio novo,
DNS ou certificado. O Apache é só o proxy TLS; **a autenticação é no próprio servidor MCP**.

## Autenticação (escala para N agentes, sem sudo)

Tokens em `data/mcp_tokens.txt` (uma linha por agente: `nome  token`). O servidor lê e
**recarrega quando o arquivo muda** — adicionar/revogar agente NÃO exige sudo nem reload
do Apache.

```bash
# adicionar um agente
echo "meu_agente  $(openssl rand -hex 32)" >> data/mcp_tokens.txt
# revogar: apague a linha do agente. (efeito em segundos)
```

## Ativar o proxy no Apache (uma vez, com sudo)

O trecho `deploy/apache/ultron-mcp-location.conf` NÃO tem segredo (só o proxy), então
pode ser aplicado direto:

```bash
VHOST=/etc/apache2/sites-available/ultron.cid-uff.net-le-ssl.conf
sudo cp "$VHOST" "$VHOST.bak.$(date +%F)"
sudo sed -i "/<\/VirtualHost>/e cat /home/marcus/desenvolvimento/baseuff/deploy/apache/ultron-mcp-location.conf" "$VHOST"
sudo apache2ctl configtest && sudo systemctl reload apache2
```

Validar:
```bash
TOKEN=$(awk '/^geral/{print $2}' /home/marcus/desenvolvimento/baseuff/data/mcp_tokens.txt)
curl -s -o /dev/null -w "sem token -> %{http_code} (401)\n" https://ultron.cid-uff.net/mcp/
curl -s -o /dev/null -w "com token -> %{http_code} (2xx/3xx)\n" \
     -H "Authorization: Bearer $TOKEN" https://ultron.cid-uff.net/mcp/
```

## Como um agente conecta

- **URL:** `https://ultron.cid-uff.net/mcp/`
- **Header:** `Authorization: Bearer <token do agente>`

Config genérica (Claude Code / SDKs MCP):
```json
{ "mcpServers": { "baseuff": {
    "url": "https://ultron.cid-uff.net/mcp/",
    "headers": { "Authorization": "Bearer <token>" }
} } }
```

hermes / openclaw / qualquer outro: mesma URL, cada um com seu token. O servidor é
agnóstico de cliente (MCP over HTTP padrão) e roda **stateless** — deploy/restart do
serviço não derruba sessões de agentes conectados.

> O conector web do claude.ai espera OAuth; para ele, evoluir do token estático para
> OAuth (FastMCP suporta) é o próximo passo, se necessário.

## ⚠️ Pegadinha deste servidor
Neste ultron, `sites-enabled/` contém **cópias** dos vhosts (não symlinks, fora do padrão
Debian). Editar em `sites-available/` **não** tem efeito — editar o arquivo em
`sites-enabled/` e recarregar. A auth Bearer é no app; use `https://ultron.cid-uff.net/mcp`.

## Contingência: e se a UFF cair (luz/internet)?

Existe uma réplica **armável** na Modal com o mesmo serving e os MESMOS tokens
(ver `docs/ARQUITETURA.md` § Réplica de contingência):

- **URL resiliente (recomendada p/ agentes novos):** `https://mcp.baseuff.workers.dev/mcp/` —
  Worker da Cloudflare que repassa para a origem UFF (latência de produção) e, em outage
  com a réplica armada, cai sozinho para a Modal. Mesmo header
  `Authorization: Bearer <token>`. Detalhes: `deploy/cloudflare/README.md`.
- **URL secundária direta da réplica** (quando armada): `https://nextmarte--baseuff-mcp.modal.run/mcp/`.
- Armar/desarmar: `./scripts/replica.sh armar [--pin]` / `desarmar` (padrão: desarmada,
  gasto zero; a flag do Worker é virada junto). Armar na véspera de dias de alta
  demanda (ex.: semana da SBPC).
