# Expor o servidor MCP na internet (sem AWS, sem DNS novo)

O `ultron.cid-uff.net` já resolve para esta máquina (200.20.5.84) e já tem TLS.
Expomos o MCP num **caminho** desse subdomínio: `https://ultron.cid-uff.net/mcp/`.
Nada de subdomínio novo, DNS ou certificado. Só um trecho no Apache.

Pré-requisitos já OK nesta máquina: Apache com `mod_proxy`, `proxy_http`, `headers`,
`setenvif`, `authz_core`; serviço `baseuff-mcp` (systemd --user) em `127.0.0.1:8088`.

## Passos (rodar com sudo no ultron)

1. **Gerar e guardar o token** (fora do git):
   ```bash
   TOKEN=$(openssl rand -hex 32); echo "$TOKEN"   # anote com segurança
   ```

2. **Inserir o trecho no vhost 443 do ultron**, com o token já substituído:
   ```bash
   SNIPPET=/home/marcus/desenvolvimento/baseuff/deploy/apache/ultron-mcp-location.conf
   VHOST=/etc/apache2/sites-available/ultron.cid-uff.net-le-ssl.conf
   sudo cp "$VHOST" "$VHOST.bak.$(date +%F)"                      # backup
   TMP=$(mktemp); sed "s/__MCP_TOKEN__/$TOKEN/" "$SNIPPET" > "$TMP"
   # insere o conteúdo de $TMP antes do </VirtualHost>
   sudo sed -i "/<\/VirtualHost>/e cat $TMP" "$VHOST"
   rm "$TMP"
   ```

3. **Testar e recarregar**:
   ```bash
   sudo apache2ctl configtest && sudo systemctl reload apache2
   ```

4. **Validar**:
   ```bash
   # sem token -> 403
   curl -s -o /dev/null -w "%{http_code}\n" https://ultron.cid-uff.net/mcp/
   # com token -> responde (MCP)
   curl -s -H "Authorization: Bearer $TOKEN" https://ultron.cid-uff.net/mcp/ | head -c 200
   ```

## Como um cliente MCP conecta

- **URL:** `https://ultron.cid-uff.net/mcp/`
- **Header:** `Authorization: Bearer <TOKEN>`

Clientes que aceitam header custom (Claude Code, mcp-remote, SDKs) conectam direto.
O conector web do claude.ai espera **OAuth** — para ele, o passo seguinte é trocar o
token estático por OAuth (FastMCP suporta; fica como evolução).

## Notas
- O `search` é request/response e passa liso pelo proxy. Se algum cliente usar streaming
  (SSE) e houver buffering, adicionar `SetEnv proxy-sendchunked 1` no `<Location /mcp>`.
- Renovação do cert (certbot) segue automática; nada aqui interfere.
- Para revogar acesso: troque o token no vhost e `reload apache2`.
