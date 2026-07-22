#!/usr/bin/env bash
# Arma/desarma a réplica de contingência do BaseUFF na Modal.
#
#   ./scripts/replica.sh armar [--pin]   # deploy (+pin = 1 container sempre quente, ~US$1/h)
#   ./scripts/replica.sh desarmar        # modal app stop — gasto zero garantido
#   ./scripts/replica.sh status          # app + último sync do Volume
#
# Desarmada é o estado padrão (nada pode subir nem cobrar). Armar só em dias de
# alta demanda que exijam uptime 100% (ex.: semana da SBPC). Armada com a UFF
# saudável também custa ~0: containers só sobem se houver outage real.
#
# O failover automático (Cloudflare Worker) lê a flag "armed" num KV. Se as vars
# CLOUDFLARE_API_TOKEN / CF_ACCOUNT_ID / CF_KV_NAMESPACE_ID estiverem no ambiente
# (ou em .env), a flag é virada aqui; senão, instruções são impressas.
set -euo pipefail
cd "$(dirname "$0")/.."

MODAL="${MODAL_BIN:-$(command -v modal || echo "$HOME/.local/bin/modal")}"
APP=baseuff-replica

# carrega credenciais opcionais do Cloudflare a partir do .env (nunca versionado)
if [[ -f .env ]]; then
  set -a; source <(grep -E '^(CLOUDFLARE_API_TOKEN|CF_ACCOUNT_ID|CF_KV_NAMESPACE_ID)=' .env || true); set +a
fi

flag_worker() { # flag_worker 1|0
  if [[ -n "${CLOUDFLARE_API_TOKEN:-}" && -n "${CF_ACCOUNT_ID:-}" && -n "${CF_KV_NAMESPACE_ID:-}" ]]; then
    curl -fsS -X PUT \
      "https://api.cloudflare.com/client/v4/accounts/${CF_ACCOUNT_ID}/storage/kv/namespaces/${CF_KV_NAMESPACE_ID}/values/armed" \
      -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" --data "$1" >/dev/null
    echo "flag do Worker: armed=$1"
  else
    echo "(Cloudflare não configurado — vire a flag manualmente:"
    echo "  npx wrangler kv key put armed $1 --namespace-id <id>  — ver deploy/cloudflare/README.md)"
  fi
}

case "${1:-}" in
  armar)
    PIN=0; [[ "${2:-}" == "--pin" ]] && PIN=1
    MODAL_REPLICA_PIN=$PIN "$MODAL" deploy deploy/modal/baseuff_replica.py
    flag_worker 1
    [[ $PIN == 1 ]] && echo "ATENÇÃO: --pin mantém 1 container CPU + 1 GPU quentes (~US\$1/h). Desarme depois!"
    echo "réplica ARMADA. Para desarmar: ./scripts/replica.sh desarmar"
    ;;
  desarmar)
    "$MODAL" app stop "$APP" || true
    flag_worker 0
    echo "réplica DESARMADA (app parado; gasto zero)."
    ;;
  status)
    "$MODAL" app list | grep -i "$APP" || echo "app $APP não está deployado/rodando"
    echo "--- último sync (manifest.json do Volume):"
    "$MODAL" volume get baseuff-data /manifest.json /dev/stdout 2>/dev/null || echo "(sem manifest — rode scripts/sync_replica.py)"
    ;;
  *)
    echo "uso: $0 {armar [--pin]|desarmar|status}" >&2
    exit 2
    ;;
esac
