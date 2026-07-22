# ruff: noqa: E501  (este módulo contém o template HTML/CSS/JS do painel — linhas longas legítimas)
"""Painel de administração do BaseUFF (saúde do serviço + analytics de consultas).

Servido pelo próprio MCP em ``/mcp/admin`` (HTML) e ``/mcp/admin/api`` (JSON), ambos
protegidos por HTTP Basic (usuário+senha). Reusa ``QueryLog`` (agregações/paginação/
drill-down), o Qdrant (saúde/índice/re-execução) e o catálogo (acervo). CPF é sempre
mascarado na saída (``mask_cpf``). Também emite chaves de acesso para novos agentes.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from pathlib import Path

import httpx
from qdrant_client import QdrantClient

from .pii import mask_cpf
from .retriever import dossier, get_document, retrieve, snippet_around

BASE_URL = "https://ultron.cid-uff.net/mcp"
_NOME_RE = re.compile(r"^[A-Za-z0-9._-]{2,32}$")


def verify_basic(authorization: str, user: str, password_sha256: str) -> bool:
    """Valida um header ``Authorization: Basic base64(user:senha)`` contra usuário + hash."""
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
    # UFF_ENCODER_URL pode ter várias URLs (uma por GPU); reporta parcial se só parte responde.
    urls = [u.strip() for u in encoder_url.split(",") if u.strip()]
    vivos = 0
    for u in urls:
        try:
            r = httpx.get(u.rstrip("/") + "/healthz", timeout=2.0)
            vivos += 1 if r.status_code == 200 and r.json().get("ok") else 0
        except Exception:
            pass
    if vivos == len(urls):
        encoder = "ok"
    elif vivos:
        encoder = f"parcial ({vivos}/{len(urls)})"
    else:
        encoder = "down"
    acervo = {}
    try:
        acervo = catalog.stats() if catalog is not None else {}
    except Exception:
        pass
    overall = "ok" if qdrant["status"] == "ok" and encoder == "ok" else "degraded"
    return {"status": overall, "qdrant": qdrant, "encoder": encoder, "acervo": acervo}


def criar_chave(tokens_path: str | None, nome: str) -> dict:
    """Emite uma chave (Bearer) para um novo agente e devolve instruções prontas de conexão.

    Anexa ``nome  token`` ao arquivo de tokens (mesmo formato do ``nova-chave.sh``); o
    servidor recarrega o arquivo sozinho (mtime), então a chave já nasce ativa.
    """
    nome = (nome or "").strip()
    if not tokens_path:
        return {"ok": False, "erro": "gestão de chaves indisponível neste servidor"}
    if not _NOME_RE.match(nome):
        return {"ok": False, "erro": "nome inválido — use 2 a 32 caracteres [A-Za-z0-9 . _ -], sem espaços"}
    p = Path(tokens_path)
    existentes = set()
    if p.exists():
        for raw in p.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#"):
                existentes.add(line.split()[0].lower())
    if nome.lower() in existentes:
        return {"ok": False, "erro": f"o agente '{nome}' já existe — escolha outro nome ou revogue antes"}
    token = secrets.token_hex(32)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(f"{nome:<8} {token}\n")
    try:
        p.chmod(0o600)
    except OSError:
        pass
    instrucoes = (
        f"Servidor MCP da Base UFF — busca no acervo ABERTO da UFF (boletins de serviço, "
        f"tutoriais do STI, editais de pesquisa).\n"
        f"Documentação (abra no navegador): {BASE_URL}\n\n"
        f"Como conectar (MCP over HTTP):\n"
        f"  URL:    {BASE_URL}\n"
        f"  Header: Authorization: Bearer {token}\n\n"
        f"Config (Claude Code / SDKs MCP):\n"
        f'{{ "mcpServers": {{ "baseuff": {{\n'
        f'    "url": "{BASE_URL}",\n'
        f'    "headers": {{ "Authorization": "Bearer {token}" }}\n'
        f"}} }} }}\n\n"
        f"Guarde esta chave: ela não é mostrada de novo. Para revogar, use ./nova-chave.sh --revogar {nome}."
    )
    return {"ok": True, "nome": nome, "token": token, "url": BASE_URL, "instrucoes": instrucoes}


def _resultado(querylog, client, collection, encoder, reranker, qid: int) -> dict:
    """Re-executa uma consulta registrada (pelo id) e devolve os resultados atuais.

    Usa a query CRUA do log (a de exibição vem mascarada), roda a MESMA tool direto no
    retriever (sem re-logar) e mascara o CPF de toda a saída."""
    out: dict = {"detalhe": "resultado", "results": []}
    row = querylog.get(qid) if querylog is not None else None
    if not row:
        out["erro"] = "consulta não encontrada"
        return out
    tool = row.get("tool")
    q = row.get("query") or ""
    source = row.get("source") or None
    df = row.get("date_from") or None
    dt = row.get("date_to") or None
    out.update({"tool": tool, "query": mask_cpf(q), "source": source, "ts": row.get("ts"), "agent": row.get("agent")})
    try:
        if tool == "search":
            if encoder is None:
                out["erro"] = "encoder indisponível — não é possível re-executar buscas"
                return out
            res = retrieve(
                client, collection, encoder, q,
                limit=10, source=source, date_from=df, date_to=dt, reranker=reranker,
            )
            out["results"] = [
                {
                    "numero": r.numero, "source": r.source, "publish_date": r.publish_date,
                    "url": r.url, "score": round(float(r.score), 3), "doc_id": r.doc_id,
                    "snippet": mask_cpf(snippet_around(r.text, q)),
                }
                for r in res
            ]
        elif tool == "sbpc":
            if encoder is None:
                out["erro"] = "encoder indisponível — não é possível re-executar buscas"
                return out
            res = retrieve(
                client, collection, encoder, q,
                limit=10, source="sbpc", date_from=df, date_to=dt,
                tipo=row.get("tipo") or None, reranker=reranker,
            )
            out["results"] = [
                {
                    "numero": r.title, "source": r.source, "publish_date": r.publish_date,
                    "url": r.url, "score": round(float(r.score), 3), "doc_id": r.doc_id,
                    "snippet": mask_cpf(snippet_around(r.text, q)),
                }
                for r in res
            ]
        elif tool == "dossie":
            d = dossier(client, collection, q, source=source or "boletim")
            for nivel in ("confirmados", "provaveis"):
                for e in d.get(nivel, []):
                    out["results"].append(
                        {
                            "nivel": nivel, "numero": e.get("numero"), "source": e.get("source"),
                            "publish_date": e.get("publish_date"), "url": e.get("url"),
                            "snippet": mask_cpf(e.get("snippet")),
                        }
                    )
            out["confirmados"] = len(d.get("confirmados", []))
            out["provaveis"] = len(d.get("provaveis", []))
        elif tool == "get_documento":
            doc = None
            try:
                doc = get_document(client, collection, doc_id=int(q))
            except (TypeError, ValueError):
                doc = get_document(client, collection, numero=q, source=source or "boletim")
            if doc:
                out["results"] = [
                    {
                        "numero": doc.get("numero"), "source": doc.get("source"),
                        "publish_date": doc.get("publish_date"), "url": doc.get("url"),
                        "n_chunks": doc.get("n_chunks"),
                        "snippet": mask_cpf((doc.get("texto") or "")[:1500]),
                    }
                ]
        else:
            out["erro"] = f"a tool '{tool}' não suporta re-execução"
    except Exception as e:  # nunca derruba o painel
        out["erro"] = f"{type(e).__name__}: {e}"
    return out


def admin_data(
    querylog, client, collection, catalog, encoder_url, params: dict,
    *, encoder=None, reranker=None, tokens_path=None,
) -> dict:
    """Roteia as requisições do painel: overview (default), drill-down, re-execução e nova chave."""

    def _int(name, default):
        try:
            return max(0, int(params.get(name, default)))
        except (TypeError, ValueError):
            return default

    if params.get("acao") == "nova_chave":
        return criar_chave(tokens_path, params.get("nome", ""))

    detalhe = params.get("detalhe")
    if detalhe in ("lacunas", "erros", "lentas"):
        rows = querylog.detail(detalhe) if querylog is not None else []
        for r in rows:
            r["query"] = mask_cpf(r.get("query"))
        return {"detalhe": detalhe, "rows": rows}
    if detalhe == "resultado":
        return _resultado(querylog, client, collection, encoder, reranker, _int("id", 0))

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


def render_logout_html() -> str:
    """Página pública de saída (sem auth) exibida após o logout do Basic Auth."""
    return _LOGOUT


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
  header{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:4px}
  h1{font-size:22px;margin:0} .sub{color:var(--muted);font-size:13px}
  .dot{width:9px;height:9px;border-radius:50%;display:inline-block;margin-right:6px;vertical-align:middle}
  .ok{background:var(--good)} .bad{background:var(--crit)} .deg{background:var(--warn)}
  .grid{display:grid;gap:14px}
  .kpis{grid-template-columns:repeat(auto-fit,minmax(150px,1fr));margin:18px 0}
  .card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:16px 18px}
  .kpi b{font-size:28px;display:block;font-variant-numeric:tabular-nums} .kpi span{color:var(--muted);font-size:12px}
  .kpi .hint{color:var(--s1);font-size:11px;display:block;margin-top:3px}
  .clickable{cursor:pointer;transition:border-color .12s ease,transform .12s ease}
  .clickable:hover{border-color:var(--s1);transform:translateY(-1px)}
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
  tr.qrow{cursor:pointer} tr.qrow:hover td{background:var(--grid)}
  .pill{padding:1px 8px;border-radius:20px;font-size:11.5px;color:#fff;white-space:nowrap}
  .tools{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:14px 0 4px}
  select,button{font:inherit;background:var(--surface);color:var(--fg);border:1px solid var(--axis);border-radius:8px;padding:6px 10px}
  button{cursor:pointer} button:disabled{opacity:.4;cursor:default}
  .btn-acc{background:var(--s1);color:#fff;border-color:var(--s1)}
  .hbtns{display:flex;gap:8px;margin-left:auto}
  .tablewrap{overflow-x:auto}
  .muted{color:var(--muted)} .q{max-width:420px}
  .foot{color:var(--muted);font-size:12px;margin-top:28px}
  svg text{fill:var(--muted);font-size:11px}
  .modal{position:fixed;inset:0;background:rgba(0,0,0,.5);display:flex;align-items:flex-start;justify-content:center;padding:40px 16px;overflow:auto;z-index:50}
  .modal[hidden]{display:none}
  .modal-card{background:var(--surface);border:1px solid var(--border);border-radius:14px;max-width:920px;width:100%;padding:20px 22px;box-shadow:0 20px 60px rgba(0,0,0,.35)}
  .modal-head{display:flex;align-items:center;gap:12px;margin-bottom:6px}
  .modal-head h2{margin:0;flex:1;color:var(--fg);font-size:17px}
  .modal-close{border:1px solid var(--axis);background:var(--surface);border-radius:8px;width:34px;height:34px;cursor:pointer;color:var(--fg);font-size:16px}
  .crumb{color:var(--muted);font-size:12.5px;margin:2px 0 12px}
  .crumb a{color:var(--s1);cursor:pointer;text-decoration:none}
  .crumb a:hover{text-decoration:underline}
  .keybox{background:var(--plane);border:1px solid var(--border);border-radius:8px;padding:12px 14px;font:12.5px/1.6 ui-monospace,Menlo,Consolas,monospace;white-space:pre-wrap;word-break:break-word;color:var(--fg);margin:10px 0}
  .res{border:1px solid var(--grid);border-radius:8px;padding:10px 12px;margin:8px 0}
  .res .rhead{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:4px}
  .res .snip{color:var(--fg2);font-size:12.5px}
  .res a,.res a:visited{color:var(--s1)}
  .field{display:flex;gap:8px;align-items:center;margin:12px 0}
  .field input{flex:1;font:inherit;padding:9px 11px;border:1px solid var(--axis);border-radius:8px;background:var(--plane);color:var(--fg)}
  .err{color:var(--crit);margin:8px 0}
  .ok-msg{color:var(--good);margin:8px 0;font-weight:600}
</style></head>
<body><div class="wrap">
<header>
  <h1>BaseUFF · Painel de Administração</h1>
  <span class="sub" id="status">carregando…</span>
  <div class="hbtns">
    <button class="btn-acc" id="btn-key">＋ Nova chave</button>
    <button id="btn-logout">Sair</button>
  </div>
</header>
<div class="sub" id="refreshed" style="margin:0 0 8px"></div>

<div class="grid health" id="health"></div>
<div class="grid kpis" id="kpis"></div>

<div class="grid charts">
  <div class="card"><h2>Consultas por dia</h2><div id="c_dia"></div></div>
  <div class="card"><h2>Por ferramenta</h2><div id="c_tool"></div></div>
  <div class="card"><h2>Por agente</h2><div id="c_agente"></div></div>
  <div class="card"><h2>Por fonte</h2><div id="c_fonte"></div></div>
</div>

<div class="card" style="margin-top:16px">
  <h2>Consultas recentes <span class="muted" style="font-weight:400">— clique numa linha para ver os resultados</span></h2>
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

<div class="modal" id="modal" hidden><div class="modal-card">
  <div class="modal-head"><h2 id="modal-title"></h2><button class="modal-close" id="modal-close">✕</button></div>
  <div id="modal-body"></div>
</div></div>

<script>
const SERIES=['--s1','--s2','--s3','--s4','--s5','--s6','--s7','--s8'];
const TOOLCLR={search:'--s1',dossie:'--s2',get_documento:'--s3',info:'--s5',sbpc:'--s4'};
const cvar=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
let state={offset:0,limit:25,agent:'',tool:''};
let rowById={};
const ADMIN=location.pathname.replace(/\/+$/,'');   // /mcp/admin
const API=ADMIN+'/api';

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
function toolPill(t){return `<span class="pill" style="background:${cvar(TOOLCLR[t]||'--s6')}">${esc(t)}</span>`;}

/* ---------- modal + drill-down ---------- */
const modal=document.getElementById('modal');
const mTitle=document.getElementById('modal-title');
const mBody=document.getElementById('modal-body');
function openModal(t){mTitle.textContent=t;modal.hidden=false;}
function closeModal(){modal.hidden=true;mBody.innerHTML='';}
document.getElementById('modal-close').onclick=closeModal;
modal.addEventListener('click',e=>{if(e.target===modal)closeModal();});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!modal.hidden)closeModal();});

async function apiGet(qs){return (await fetch(API+'?'+qs,{cache:'no-store'})).json();}

const DET_TITLE={lacunas:'Lacunas — consultas sem resultado',erros:'Erros',lentas:'Consultas mais lentas'};
async function showDetail(kind){
  openModal(DET_TITLE[kind]||kind);
  mBody.innerHTML='<div class="muted">carregando…</div>';
  let d; try{d=await apiGet('detalhe='+kind);}catch(e){mBody.innerHTML='<div class="err">falha ao carregar</div>';return;}
  const rows=d.rows||[];
  if(!rows.length){mBody.innerHTML='<div class="muted">nada aqui — ótimo sinal.</div>';return;}
  const head='<div class="crumb">clique numa consulta para re-executar e ver os resultados atuais</div>';
  mBody.innerHTML=head+'<div class="tablewrap"><table><thead><tr><th>quando</th><th>agente</th><th>tool</th><th>consulta</th><th>fonte</th><th class="num">res.</th><th class="num">ms</th>'+(kind==='erros'?'<th>erro</th>':'')+'</tr></thead><tbody>'+
    rows.map(r=>`<tr class="qrow" data-id="${r.id}"><td class="muted">${esc((r.ts||'').slice(0,19))}</td><td>${esc(r.agent)}</td><td>${toolPill(r.tool)}</td><td class="q">${esc(r.query)}</td><td class="muted">${esc(r.source||'—')}</td><td class="num">${r.n_results==null?'—':r.n_results}</td><td class="num">${r.latency_ms==null?'—':r.latency_ms}</td>${kind==='erros'?`<td class="err">${esc(r.error||'')}</td>`:''}</tr>`).join('')+
    '</tbody></table></div>';
  mBody.querySelectorAll('tr.qrow').forEach(tr=>tr.onclick=()=>showResults(tr.dataset.id,kind));
}

function resultCard(r){
  const meta=[r.nivel?`<span class="pill" style="background:${cvar(r.nivel==='confirmados'?'--s2':'--s3')}">${esc(r.nivel)}</span>`:'',
    r.numero?`<b>${esc(r.numero)}</b>`:'', r.publish_date?`<span class="muted">${esc(r.publish_date)}</span>`:'',
    r.source?`<span class="muted">${esc(r.source)}</span>`:'', r.score!=null?`<span class="muted">score ${esc(r.score)}</span>`:'',
    r.n_chunks!=null?`<span class="muted">${esc(r.n_chunks)} trechos</span>`:'',
    r.url?`<a href="${esc(r.url)}" target="_blank" rel="noopener">abrir ▸</a>`:''].filter(Boolean).join(' ');
  return `<div class="res"><div class="rhead">${meta}</div><div class="snip">${esc(r.snippet)}</div></div>`;
}
async function showResults(id, back){
  openModal('Resultados da consulta');
  mBody.innerHTML='<div class="muted">re-executando a consulta…</div>';
  let d; try{d=await apiGet('detalhe=resultado&id='+encodeURIComponent(id));}catch(e){mBody.innerHTML='<div class="err">falha ao re-executar</div>';return;}
  const crumb=back?`<div class="crumb"><a id="back">◀ voltar</a> · re-executado agora</div>`:'<div class="crumb">re-executado agora (resultados atuais da base)</div>';
  let hd=`<div style="margin-bottom:10px">${toolPill(d.tool)} <b>${esc(d.query)}</b>${d.source?` <span class="muted">· fonte ${esc(d.source)}</span>`:''}`;
  if(d.confirmados!=null) hd+=` <span class="muted">· ${d.confirmados} confirmados / ${d.provaveis} prováveis</span>`;
  hd+='</div>';
  let body;
  if(d.erro) body=`<div class="err">${esc(d.erro)}</div>`;
  else if(!d.results||!d.results.length) body='<div class="muted">nenhum resultado — é uma lacuna real da base.</div>';
  else body=d.results.map(resultCard).join('');
  mBody.innerHTML=crumb+hd+body;
  const b=document.getElementById('back'); if(b) b.onclick=()=>showDetail(back);
}

/* ---------- nova chave ---------- */
function novaChaveForm(){
  openModal('Gerar chave para novo agente');
  mBody.innerHTML=`<div class="crumb">Crie uma chave (Bearer) e envie as instruções ao novo agente. A chave nasce ativa — sem reiniciar nada.</div>
    <div class="field"><input id="nome" placeholder="nome do agente (ex.: hermes, openclaw)" autocomplete="off" maxlength="32"><button class="btn-acc" id="gera">Gerar</button></div>
    <div id="key-out"></div>`;
  const inp=document.getElementById('nome'); inp.focus();
  const go=async()=>{
    const nome=inp.value.trim();
    const out=document.getElementById('key-out');
    if(!nome){out.innerHTML='<div class="err">informe um nome.</div>';return;}
    out.innerHTML='<div class="muted">gerando…</div>';
    let d; try{d=await (await fetch(API+'?acao=nova_chave&nome='+encodeURIComponent(nome),{method:'POST',cache:'no-store'})).json();}
    catch(e){out.innerHTML='<div class="err">falha ao gerar</div>';return;}
    if(!d.ok){out.innerHTML=`<div class="err">${esc(d.erro||'erro')}</div>`;return;}
    out.innerHTML=`<div class="ok-msg">✔ Chave criada para “${esc(d.nome)}” — já ativa.</div>
      <div class="field"><button id="copy">Copiar instruções</button><span class="muted" id="copied"></span></div>
      <div class="keybox" id="instr">${esc(d.instrucoes)}</div>`;
    document.getElementById('copy').onclick=()=>{navigator.clipboard.writeText(d.instrucoes).then(()=>{document.getElementById('copied').textContent='copiado ✓';});};
  };
  document.getElementById('gera').onclick=go;
  inp.onkeydown=e=>{if(e.key==='Enter')go();};
}

/* ---------- logout ---------- */
function logout(){
  // invalida as credenciais Basic em cache (requisição síncrona com usuário/senha inválidos)
  try{const x=new XMLHttpRequest();x.open('GET',API,false,'logout','x');x.send();}catch(e){}
  location.href=ADMIN+'/logout';
}
document.getElementById('btn-key').onclick=novaChaveForm;
document.getElementById('btn-logout').onclick=logout;

/* ---------- carga principal ---------- */
async function load(){
  const p=new URLSearchParams(state);
  let d; try{ d=await (await fetch(API+'?'+p,{cache:'no-store'})).json(); }
  catch(e){ document.getElementById('status').innerHTML='<span class="dot bad"></span>erro ao carregar'; return; }
  const h=d.health, ov=h.status==='ok'?'ok':'deg';
  document.getElementById('status').innerHTML=`<span class="dot ${ov==='ok'?'ok':'deg'}"></span>${ov==='ok'?'operacional':'degradado'}`;
  document.getElementById('refreshed').textContent='atualizado '+new Date().toLocaleTimeString('pt-BR');
  const bol=h.acervo.boletim||{};
  document.getElementById('health').innerHTML=[
    ['Serviço MCP', statusDot('ok')],
    ['Qdrant (índice)', statusDot(h.qdrant.status)+(h.qdrant.chunks?` · ${h.qdrant.chunks.toLocaleString('pt-BR')} chunks`:'')],
    ['Encoder (skynet01)', statusDot(h.encoder)],
    ['Acervo boletim', (bol.documentos||0).toLocaleString('pt-BR')+' docs · '+(bol.data_inicial||'?').slice(0,4)+'–'+(bol.data_final||'?').slice(0,4)],
  ].map(([k,v])=>`<div class="card hline"><span class="hlabel">${k}:</span> <b>${v}</b></div>`).join('');
  const a=d.agg, L=a.latencia||{};
  const kpi=[
    ['Consultas', a.total, null],['Latência p50', (L.p50||0)+'ms', 'lentas'],['Latência p95', (L.p95||0)+'ms', 'lentas'],
    ['Agentes ativos', a.agentes, null],['Lacunas', a.lacunas, 'lacunas'],['Erros', a.erros, 'erros'],
  ];
  document.getElementById('kpis').innerHTML=kpi.map(([k,v,det])=>
    `<div class="card kpi${det?' clickable':''}"${det?` data-det="${det}"`:''}><b>${v}</b><span>${k}</span>${det?'<span class="hint">ver detalhes ▸</span>':''}</div>`
  ).join('');
  areaDia(document.getElementById('c_dia'), a.por_dia||[]);
  bars(document.getElementById('c_tool'), a.por_tool||[], k=>TOOLCLR[k]||'--s6');
  bars(document.getElementById('c_agente'), a.por_agente||[], (k,i)=>SERIES[i%8]);
  bars(document.getElementById('c_fonte'), a.por_fonte||[], (k,i)=>SERIES[i%8]);
  const fa=document.getElementById('f_agent'), ft=document.getElementById('f_tool');
  if(fa.options.length<=1) (a.por_agente||[]).forEach(x=>fa.add(new Option(x[0],x[0])));
  if(ft.options.length<=1) (a.por_tool||[]).forEach(x=>ft.add(new Option(x[0],x[0])));
  const pg=d.pagina; rowById={};
  document.getElementById('rows').innerHTML=pg.rows.map(r=>{
    rowById[r.id]=r;
    return `<tr class="qrow" data-id="${r.id}"><td class="muted">${esc((r.ts||'').slice(5,19))}</td><td>${esc(r.agent)}</td>`+
      `<td>${toolPill(r.tool)}</td>`+
      `<td class="q">${esc(r.query)}</td><td class="muted">${esc(r.source||'—')}</td>`+
      `<td class="num">${r.n_results==null?'—':r.n_results}</td><td class="num">${r.latency_ms==null?'—':r.latency_ms}</td></tr>`;
  }).join('')||'<tr><td colspan="7" class="muted">sem consultas</td></tr>';
  document.querySelectorAll('#rows tr.qrow').forEach(tr=>tr.onclick=()=>showResults(tr.dataset.id));
  const from=pg.total?pg.offset+1:0, to=Math.min(pg.total,pg.offset+pg.limit);
  document.getElementById('pageinfo').textContent=`${from}–${to} de ${pg.total}`;
  document.getElementById('prev').disabled=pg.offset<=0;
  document.getElementById('next').disabled=to>=pg.total;
}
document.getElementById('kpis').addEventListener('click',e=>{const c=e.target.closest('[data-det]');if(c)showDetail(c.dataset.det);});
document.getElementById('prev').onclick=()=>{state.offset=Math.max(0,state.offset-state.limit);load()};
document.getElementById('next').onclick=()=>{state.offset+=state.limit;load()};
document.getElementById('f_agent').onchange=e=>{state.agent=e.target.value;state.offset=0;load()};
document.getElementById('f_tool').onchange=e=>{state.tool=e.target.value;state.offset=0;load()};
load(); setInterval(load,30000);
</script></body></html>"""


_LOGOUT = r"""<!doctype html>
<html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BaseUFF · Sessão encerrada</title>
<style>
  :root{--plane:#f9f9f7;--surface:#fcfcfb;--fg:#0b0b0b;--muted:#898781;--border:rgba(11,11,11,.10);--s1:#2a78d6}
  @media (prefers-color-scheme:dark){:root{--plane:#0d0d0d;--surface:#1a1a19;--fg:#fff;--muted:#898781;--border:rgba(255,255,255,.10);--s1:#3987e5}}
  body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;font:15px/1.6 system-ui,-apple-system,sans-serif;background:var(--plane);color:var(--fg)}
  .box{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:32px 36px;text-align:center;max-width:420px}
  h1{font-size:20px;margin:0 0 8px} p{color:var(--muted);margin:0 0 20px}
  a{display:inline-block;background:var(--s1);color:#fff;text-decoration:none;padding:10px 18px;border-radius:9px}
</style></head>
<body><div class="box">
  <h1>Sessão encerrada</h1>
  <p>Você saiu do painel de administração. Para encerrar completamente em alguns navegadores, feche a janela.</p>
  <a href="../admin">Entrar novamente</a>
</div></body></html>"""
