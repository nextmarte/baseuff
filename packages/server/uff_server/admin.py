# ruff: noqa: E501  (este módulo contém o template HTML/CSS/JS do painel — linhas longas legítimas)
"""Painel de administração do BaseUFF (saúde do serviço + analytics de consultas).

Servido pelo próprio MCP em ``/mcp/admin`` (HTML) e ``/mcp/admin/api`` (JSON), ambos
protegidos por HTTP Basic (usuário+senha). Reusa ``QueryLog`` (agregações/paginação),
o Qdrant (saúde/índice) e o catálogo (acervo). CPF é mascarado na saída (``mask_cpf``).
"""

from __future__ import annotations

import hashlib
import hmac

import httpx
from qdrant_client import QdrantClient

from .pii import mask_cpf


def verify_basic(authorization: str, user: str, password_sha256: str) -> bool:
    """Valida um header ``Authorization: Basic base64(user:senha)`` contra usuário + hash."""
    import base64

    parts = (authorization or "").split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "basic":
        return False
    try:
        decoded = base64.b64decode(parts[1]).decode("utf-8", "replace")
        u, _, p = decoded.partition(":")
    except Exception:
        return False
    got = hashlib.sha256(p.encode()).hexdigest()
    return hmac.compare_digest(u, user) and hmac.compare_digest(got, password_sha256)


def _health(client: QdrantClient, collection: str, catalog, encoder_url: str) -> dict:
    qdrant = {"status": "down"}
    try:
        info = client.get_collection(collection)
        qdrant = {
            "status": "ok",
            "chunks": client.count(collection).count,
            "index": str(getattr(info, "status", "")),
        }
    except Exception:
        pass
    encoder = "down"
    try:
        r = httpx.get(encoder_url.rstrip("/") + "/healthz", timeout=2.0)
        encoder = "ok" if r.status_code == 200 and r.json().get("ok") else "down"
    except Exception:
        encoder = "down"
    acervo = {}
    try:
        acervo = catalog.stats() if catalog is not None else {}
    except Exception:
        pass
    overall = "ok" if qdrant["status"] == "ok" and encoder == "ok" else "degraded"
    return {"status": overall, "qdrant": qdrant, "encoder": encoder, "acervo": acervo}


def admin_data(querylog, client, collection, catalog, encoder_url, params: dict) -> dict:
    """Payload do painel: saúde + agregados + uma página de consultas (filtrável)."""

    def _int(name, default):
        try:
            return max(0, int(params.get(name, default)))
        except (TypeError, ValueError):
            return default

    limit = min(100, _int("limit", 25))
    offset = _int("offset", 0)
    agent = params.get("agent") or None
    tool = params.get("tool") or None

    agg = querylog.aggregates() if querylog is not None else {"total": 0}
    total, rows = querylog.page(limit, offset, agent, tool) if querylog else (0, [])
    for r in rows:
        r["query"] = mask_cpf(r.get("query"))
    return {
        "health": _health(client, collection, catalog, encoder_url),
        "agg": agg,
        "pagina": {
            "total": total,
            "limit": limit,
            "offset": offset,
            "agent": agent,
            "tool": tool,
            "rows": rows,
        },
    }


def render_admin_html() -> str:
    """Página do painel (HTML+CSS+JS autocontido). Busca os dados em ``/mcp/admin/api``."""
    return _PAGE


_PAGE = r"""<!doctype html>
<html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BaseUFF · Painel de Administração</title>
<style>
  :root{
    --plane:#f9f9f7; --surface:#fcfcfb; --fg:#0b0b0b; --fg2:#52514e; --muted:#898781;
    --grid:#e1e0d9; --axis:#c3c2b7; --border:rgba(11,11,11,.10);
    --s1:#2a78d6; --s2:#1baf7a; --s3:#eda100; --s4:#008300; --s5:#4a3aa7; --s6:#e34948; --s7:#e87ba4; --s8:#eb6834;
    --good:#0ca30c; --warn:#fab219; --crit:#d03b3b;
  }
  @media (prefers-color-scheme:dark){:root{
    --plane:#0d0d0d; --surface:#1a1a19; --fg:#fff; --fg2:#c3c2b7; --muted:#898781;
    --grid:#2c2c2a; --axis:#383835; --border:rgba(255,255,255,.10);
    --s1:#3987e5; --s2:#199e70; --s3:#c98500; --s4:#008300; --s5:#9085e9; --s6:#e66767; --s7:#d55181; --s8:#d95926;
  }}
  *{box-sizing:border-box}
  body{margin:0;font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;background:var(--plane);color:var(--fg)}
  .wrap{max-width:1200px;margin:0 auto;padding:24px 20px 64px}
  header{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:4px}
  h1{font-size:22px;margin:0} .sub{color:var(--muted);font-size:13px}
  .dot{width:9px;height:9px;border-radius:50%;display:inline-block;margin-right:6px;vertical-align:middle}
  .ok{background:var(--good)} .bad{background:var(--crit)} .deg{background:var(--warn)}
  .grid{display:grid;gap:14px}
  .kpis{grid-template-columns:repeat(auto-fit,minmax(150px,1fr));margin:18px 0}
  .card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:16px 18px}
  .kpi b{font-size:28px;display:block;font-variant-numeric:tabular-nums} .kpi span{color:var(--muted);font-size:12px}
  .health{grid-template-columns:repeat(auto-fit,minmax(180px,1fr));margin-bottom:4px}
  .hline{display:flex;align-items:center;gap:6px} .hlabel{color:var(--fg2)}
  .charts{grid-template-columns:repeat(auto-fit,minmax(320px,1fr));margin-top:14px}
  h2{font-size:14px;margin:0 0 10px;color:var(--fg2);font-weight:600}
  .bar-row{display:grid;grid-template-columns:130px 1fr 46px;align-items:center;gap:8px;margin:6px 0}
  .bar-lab{color:var(--fg2);font-size:12.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .bar-track{height:16px;background:var(--grid);border-radius:4px;overflow:hidden}
  .bar-fill{height:100%;border-radius:4px}
  .bar-val{text-align:right;font-variant-numeric:tabular-nums;color:var(--fg2);font-size:12.5px}
  table{width:100%;border-collapse:collapse;margin-top:8px;font-size:13px}
  th{text-align:left;color:var(--muted);font-weight:600;border-bottom:1px solid var(--axis);padding:8px 8px;position:sticky;top:0;background:var(--surface)}
  td{padding:8px 8px;border-bottom:1px solid var(--grid);vertical-align:top}
  td.num{text-align:right;font-variant-numeric:tabular-nums}
  .pill{padding:1px 8px;border-radius:20px;font-size:11.5px;color:#fff;white-space:nowrap}
  .tools{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:14px 0 4px}
  select,button{font:inherit;background:var(--surface);color:var(--fg);border:1px solid var(--axis);border-radius:8px;padding:6px 10px}
  button{cursor:pointer} button:disabled{opacity:.4;cursor:default}
  .tablewrap{overflow-x:auto}
  .muted{color:var(--muted)} .q{max-width:420px}
  .foot{color:var(--muted);font-size:12px;margin-top:28px}
  svg text{fill:var(--muted);font-size:11px}
</style></head>
<body><div class="wrap">
<header>
  <h1>BaseUFF · Painel de Administração</h1>
  <span class="sub" id="status">carregando…</span>
  <span class="sub" style="margin-left:auto" id="refreshed"></span>
</header>

<div class="grid health" id="health"></div>
<div class="grid kpis" id="kpis"></div>

<div class="grid charts">
  <div class="card"><h2>Consultas por dia</h2><div id="c_dia"></div></div>
  <div class="card"><h2>Por ferramenta</h2><div id="c_tool"></div></div>
  <div class="card"><h2>Por agente</h2><div id="c_agente"></div></div>
  <div class="card"><h2>Por fonte</h2><div id="c_fonte"></div></div>
</div>

<div class="card" style="margin-top:16px">
  <h2>Consultas recentes</h2>
  <div class="tools">
    <label class="muted">agente <select id="f_agent"><option value="">todos</option></select></label>
    <label class="muted">ferramenta <select id="f_tool"><option value="">todas</option></select></label>
    <span style="margin-left:auto"></span>
    <button id="prev">◀ anterior</button>
    <span id="pageinfo" class="muted"></span>
    <button id="next">próxima ▶</button>
  </div>
  <div class="tablewrap"><table id="tbl">
    <thead><tr><th>quando</th><th>agente</th><th>tool</th><th>consulta</th><th>fonte</th><th class="num">res.</th><th class="num">ms</th></tr></thead>
    <tbody id="rows"></tbody>
  </table></div>
</div>
<div class="foot">Atualiza a cada 30s · dados de <code>data/queries.db</code> · CPF anonimizado. Painel privado.</div>
</div>
<script>
const SERIES=['--s1','--s2','--s3','--s4','--s5','--s6','--s7','--s8'];
const TOOLCLR={search:'--s1',dossie:'--s2',get_documento:'--s3',info:'--s5'};
const cvar=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
let state={offset:0,limit:25,agent:'',tool:''};
const API=location.pathname.replace(/\/+$/,'')+'/api';  // /mcp/admin -> /mcp/admin/api

function bars(el, data, colorFn){
  const max=Math.max(1,...data.map(d=>d[1]));
  el.innerHTML=data.map((d,i)=>{
    const w=(100*d[1]/max).toFixed(1), col=cvar(colorFn(d[0],i));
    return `<div class="bar-row"><div class="bar-lab" title="${esc(d[0])}">${esc(d[0])}</div>`+
      `<div class="bar-track"><div class="bar-fill" style="width:${w}%;background:${col}"></div></div>`+
      `<div class="bar-val">${d[1]}</div></div>`;
  }).join('')||'<div class="muted">sem dados</div>';
}
function areaDia(el, data){
  if(!data.length){el.innerHTML='<div class="muted">sem dados</div>';return}
  const W=Math.max(320,el.clientWidth||320),H=140,P=24,max=Math.max(1,...data.map(d=>d[1]));
  const x=i=>P+(W-2*P)*(data.length<2?0.5:i/(data.length-1)), y=v=>H-P-(H-2*P)*v/max;
  const pts=data.map((d,i)=>[x(i),y(d[1])]);
  const line=pts.map((p,i)=>(i?'L':'M')+p[0].toFixed(1)+' '+p[1].toFixed(1)).join(' ');
  const area=`M${x(0).toFixed(1)} ${H-P} `+pts.map(p=>'L'+p[0].toFixed(1)+' '+p[1].toFixed(1)).join(' ')+` L${x(data.length-1).toFixed(1)} ${H-P} Z`;
  const c=cvar('--s1');
  const ticks=data.map((d,i)=>`<text x="${x(i)}" y="${H-6}" text-anchor="middle">${esc(d[0].slice(5))}</text><circle cx="${x(i)}" cy="${y(d[1])}" r="3.5" fill="${c}"/><text x="${x(i)}" y="${y(d[1])-8}" text-anchor="middle" style="fill:var(--fg2)">${d[1]}</text>`).join('');
  el.innerHTML=`<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}"><line x1="${P}" y1="${H-P}" x2="${W-P}" y2="${H-P}" stroke="${cvar('--axis')}"/><path d="${area}" fill="${c}" opacity="0.12"/><path d="${line}" fill="none" stroke="${c}" stroke-width="2"/>${ticks}</svg>`;
}
function statusDot(s){return s==='ok'?'<span class="dot ok"></span>ok':(s==='down'?'<span class="dot bad"></span>fora':'<span class="dot deg"></span>'+esc(s));}

async function load(){
  const p=new URLSearchParams(state);
  let d; try{ d=await (await fetch(API+'?'+p,{cache:'no-store'})).json(); }
  catch(e){ document.getElementById('status').innerHTML='<span class="dot bad"></span>erro ao carregar'; return; }
  const h=d.health, ov=h.status==='ok'?'ok':'deg';
  document.getElementById('status').innerHTML=`<span class="dot ${ov==='ok'?'ok':'deg'}"></span>${ov==='ok'?'operacional':'degradado'}`;
  document.getElementById('refreshed').textContent='atualizado '+new Date().toLocaleTimeString('pt-BR');
  // health
  const bol=h.acervo.boletim||{};
  document.getElementById('health').innerHTML=[
    ['Serviço MCP', statusDot('ok')],
    ['Qdrant (índice)', statusDot(h.qdrant.status)+(h.qdrant.chunks?` · ${h.qdrant.chunks.toLocaleString('pt-BR')} chunks`:'')],
    ['Encoder (skynet01)', statusDot(h.encoder)],
    ['Acervo boletim', (bol.documentos||0).toLocaleString('pt-BR')+' docs · '+(bol.data_inicial||'?').slice(0,4)+'–'+(bol.data_final||'?').slice(0,4)],
  ].map(([k,v])=>`<div class="card hline"><span class="hlabel">${k}:</span> <b>${v}</b></div>`).join('');
  // kpis
  const a=d.agg, L=a.latencia||{};
  document.getElementById('kpis').innerHTML=[
    ['Consultas', a.total],['Latência p50', (L.p50||0)+'ms'],['Latência p95', (L.p95||0)+'ms'],
    ['Agentes ativos', a.agentes],['Lacunas', a.lacunas],['Erros', a.erros],
  ].map(([k,v])=>`<div class="card kpi"><b>${v}</b><span>${k}</span></div>`).join('');
  // charts
  areaDia(document.getElementById('c_dia'), a.por_dia||[]);
  bars(document.getElementById('c_tool'), a.por_tool||[], k=>TOOLCLR[k]||'--s6');
  bars(document.getElementById('c_agente'), a.por_agente||[], (k,i)=>SERIES[i%8]);
  bars(document.getElementById('c_fonte'), a.por_fonte||[], (k,i)=>SERIES[i%8]);
  // filtros (popular uma vez)
  const fa=document.getElementById('f_agent'), ft=document.getElementById('f_tool');
  if(fa.options.length<=1) (a.por_agente||[]).forEach(x=>fa.add(new Option(x[0],x[0])));
  if(ft.options.length<=1) (a.por_tool||[]).forEach(x=>ft.add(new Option(x[0],x[0])));
  // tabela
  const pg=d.pagina;
  document.getElementById('rows').innerHTML=pg.rows.map(r=>{
    const col=cvar(TOOLCLR[r.tool]||'--s6');
    return `<tr><td class="muted">${esc((r.ts||'').slice(5,19))}</td><td>${esc(r.agent)}</td>`+
      `<td><span class="pill" style="background:${col}">${esc(r.tool)}</span></td>`+
      `<td class="q">${esc(r.query)}</td><td class="muted">${esc(r.source||'—')}</td>`+
      `<td class="num">${r.n_results==null?'—':r.n_results}</td><td class="num">${r.latency_ms==null?'—':r.latency_ms}</td></tr>`;
  }).join('')||'<tr><td colspan="7" class="muted">sem consultas</td></tr>';
  const from=pg.total?pg.offset+1:0, to=Math.min(pg.total,pg.offset+pg.limit);
  document.getElementById('pageinfo').textContent=`${from}–${to} de ${pg.total}`;
  document.getElementById('prev').disabled=pg.offset<=0;
  document.getElementById('next').disabled=to>=pg.total;
}
document.getElementById('prev').onclick=()=>{state.offset=Math.max(0,state.offset-state.limit);load()};
document.getElementById('next').onclick=()=>{state.offset+=state.limit;load()};
document.getElementById('f_agent').onchange=e=>{state.agent=e.target.value;state.offset=0;load()};
document.getElementById('f_tool').onchange=e=>{state.tool=e.target.value;state.offset=0;load()};
load(); setInterval(load,30000);
</script></body></html>"""
