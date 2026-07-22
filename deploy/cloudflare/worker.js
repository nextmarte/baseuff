// Failover do BaseUFF MCP: mesma URL pública, origem na UFF com fallback para a
// réplica Modal quando a UFF cai (luz/internet). Só atua com a flag KV armed=1
// (virada por scripts/replica.sh); desarmado, é um proxy transparente da origem.

// Timeout SÓ até os headers chegarem (Promise.race, sem AbortSignal): não pode
// derrubar streams SSE longos do MCP streamable-http.
const comTimeout = (promessa, ms) =>
  Promise.race([
    promessa,
    new Promise((_, rej) => setTimeout(() => rej(new Error("timeout da origem")), ms)),
  ]);

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    // Barra final no endpoint MCP gera 307 na origem com Location interno quebrado
    // (https://127.0.0.1:8088/mcp) — normalizamos antes de encaminhar.
    const path = url.pathname === "/mcp/" ? "/mcp" : url.pathname;
    const alvo = (base) => base + path + url.search;
    // Corpo bufferizado: se a origem falhar já tendo consumido o corpo, ainda
    // conseguimos reenviar o MESMO corpo à réplica (requests MCP são JSON pequenos).
    const body = ["GET", "HEAD"].includes(request.method)
      ? undefined
      : await request.arrayBuffer();
    const init = { method: request.method, headers: request.headers, body };

    const armado = (await env.FLAGS.get("armed")) === "1";
    if (!armado) return fetch(alvo(env.ORIGIN_URL), init);

    try {
      const resp = await comTimeout(
        fetch(alvo(env.ORIGIN_URL), init),
        Number(env.ORIGIN_TIMEOUT_MS || 5000),
      );
      if (resp.status < 500) return resp;
    } catch (_) {
      // origem fora do ar — cai para a réplica
    }
    return fetch(alvo(env.REPLICA_URL), init);
  },

  // Cron 1/min (efetivo só quando armado): se a origem caiu, aquece a réplica para
  // a 1ª consulta real não pagar cold start. Bater em /mcp/docs (público) sobe o
  // container do MCP, que por sua vez já dispara o warmup da GPU em background.
  async scheduled(event, env, ctx) {
    if ((await env.FLAGS.get("armed")) !== "1") return;
    try {
      const r = await comTimeout(fetch(env.ORIGIN_URL + "/mcp/docs"), 5000);
      if (r.status < 500) return; // origem saudável — não acordar a réplica à toa
    } catch (_) {}
    ctx.waitUntil(fetch(env.REPLICA_URL + "/mcp/docs").catch(() => {}));
  },
};
