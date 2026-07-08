#!/usr/bin/env bash
# Gera/gerencia chaves de acesso ao MCP BaseUFF (uma por agente/pessoa).
# O servidor recarrega o arquivo de tokens automaticamente — não precisa reiniciar nada.
#
#   ./nova-chave.sh <nome>          # cria uma chave nova para <nome> e imprime as instruções
#   ./nova-chave.sh --listar        # lista os agentes cadastrados (token mascarado)
#   ./nova-chave.sh --revogar <nome> # revoga (remove) a chave de <nome>
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOKENS="$REPO/data/mcp_tokens.txt"
URL="https://ultron.cid-uff.net/mcp"
mkdir -p "$(dirname "$TOKENS")"; touch "$TOKENS"

listar() {
  echo "Agentes cadastrados:"
  grep -vE '^\s*#|^\s*$' "$TOKENS" | awk '{printf "  %-14s %s…\n", $1, substr($2,1,8)}'
}

case "${1:-}" in
  ""|-h|--help|--ajuda)
    grep -E '^#' "$0" | sed 's/^# \{0,1\}//' | tail -n +2; exit 0 ;;
  --listar|-l) listar; exit 0 ;;
  --revogar)
    nome="${2:?uso: ./nova-chave.sh --revogar <nome>}"
    if grep -qE "^${nome}[[:space:]]" "$TOKENS"; then
      sed -i "/^${nome}[[:space:]]/d" "$TOKENS"
      echo "✅ Chave de '$nome' revogada (efeito em segundos)."
    else
      echo "Agente '$nome' não encontrado."; exit 1
    fi
    exit 0 ;;
esac

NOME="$1"
if [[ "$NOME" == *[[:space:]]* ]]; then echo "Nome não pode ter espaços."; exit 1; fi
if grep -qE "^${NOME}[[:space:]]" "$TOKENS"; then
  echo "⚠️  Agente '$NOME' já existe. Use outro nome ou revogue antes (--revogar $NOME)."; exit 1
fi

TOKEN="$(openssl rand -hex 32)"
printf '%-8s %s\n' "$NOME" "$TOKEN" >> "$TOKENS"
chmod 600 "$TOKENS"

cat <<EOF
✅ Chave criada para '$NOME' (já ativa; sem reiniciar nada).

—— Envie estas instruções para a pessoa: ——

Servidor MCP da Base UFF (busca no acervo aberto da UFF: boletins, tutoriais do STI, editais).
Documentação (abra no navegador): $URL

Como conectar (MCP over HTTP):
  URL:    $URL
  Header: Authorization: Bearer $TOKEN

Config genérica (Claude Code / SDKs MCP):
{ "mcpServers": { "baseuff": {
    "url": "$URL",
    "headers": { "Authorization": "Bearer $TOKEN" }
} } }

Teste rápido:
  curl -sL -X POST $URL -H "Authorization: Bearer $TOKEN" \\
    -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \\
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"$NOME","version":"1"}}}'
———————————————————————————————————————————
EOF
