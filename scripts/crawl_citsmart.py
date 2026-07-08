"""Crawler da Base de Conhecimento do STI (CITSmart) — modo VISITANTE via Playwright.

O portal é um SPA Angular que exige uma sessão/handshake do navegador; um cliente
HTTP puro cai na tela de login. O Playwright abre a sessão guest e então:

  1. /rest/citajax/folder/findFolderUserCanAccess -> árvore de pastas (JSON)
  2. .event findKnowledgeByIdFolder(idFolder)     -> artigos de cada pasta
  3. .event getKnowledgeById(idKnowledgeBase)      -> conteúdo HTML do artigo

Cada artigo é salvo como HTML em data/raw/sti_kb/{doc_id}.html e registrado no
catálogo (FETCHED), reaproveitando o pipeline de indexação (parse_any -> HTML).
O caminho da pasta vira o "órgão" (contexto). Uso:

    uv run --with playwright python scripts/crawl_citsmart.py --limit 10
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib

from playwright.sync_api import sync_playwright
from uff_core.catalog import Catalog
from uff_core.config import Settings, sqlite_path
from uff_core.schemas import DocStatus, Document, Source

BASE = "https://citsmart.uff.br/citsmart"
PORTAL = f"{BASE}/pages/knowledgeBasePortal/knowledgeBasePortal.load"
KB_EVENT = f"{BASE}/knowledgeBasePortal/knowledgeBasePortal.event"


_FRAME = "\x00\x01\x02\x03\x04\x05\x06\x07 ;\n\r\t"


def _parse_callback(text: str):
    """Extrai o JSON de ``…scriptresultForCallback = {…}…`` (com chars de enquadramento)."""
    idx = text.find("resultForCallback")
    if idx < 0:
        return None
    payload = text[idx:].split("=", 1)[1].strip().strip(_FRAME)
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


def _event(req, method: str, **params):
    form = {
        "method": "execute",
        "parmCount": "",
        "parm1": "knowledgeBasePortal",
        "parm2": "",
        "parm3": method,
        **{k: str(v) for k, v in params.items()},
    }
    resp = req.post(KB_EVENT, form=form, timeout=30000)
    return _parse_callback(resp.text())


def _list_folder(req, id_folder: int, selected_page: int) -> dict:
    """Lista (paginada) os artigos de uma pasta; ``selected_page`` é 1-based."""
    result = _event(
        req,
        "findKnowledgeByIdFolder",
        idFolder=id_folder,
        filterText="",
        selectedPage=selected_page,
    )
    return result if isinstance(result, dict) else {"total": 0, "content": []}


def _folder_tree(req) -> list[dict]:
    resp = req.post(
        f"{BASE}/rest/citajax/folder/findFolderUserCanAccess",
        data=json.dumps({"realUrl": "/citsmart/folder/folder.load"}),
        headers={"Content-Type": "application/json"},
        timeout=30000,
    )
    return resp.json()


def _paths(folders: list[dict]) -> dict[int, str]:
    by_id = {f["idFolder"]: f for f in folders if f.get("idFolder")}

    def path(fid: int, seen: set[int]) -> str:
        f = by_id.get(fid)
        if not f or fid in seen:
            return ""
        seen.add(fid)
        parent = f.get("idParentFolder")
        prefix = path(parent, seen) if parent else ""
        name = (f.get("name") or "").strip()
        return f"{prefix} / {name}" if prefix else name

    return {fid: path(fid, set()) for fid in by_id}


def run(limit: int | None, data_dir: str, catalog: Catalog) -> None:
    raw_dir = pathlib.Path(data_dir) / "raw" / Source.STI_KB.value
    raw_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(ignore_https_errors=True)
        page = ctx.new_page()
        # "load" (não "networkidle"): o portal tem heartbeat periódico e a rede
        # nunca fica ociosa, o que penduraria o goto indefinidamente.
        page.goto(PORTAL, wait_until="load", timeout=45000)
        req = ctx.request

        folders = _folder_tree(req)
        paths = _paths(folders)
        roots = [
            f["idFolder"] for f in folders if not f.get("idParentFolder") and f.get("idFolder")
        ]
        root = roots[0] if roots else 58
        print(f"[citsmart] {len(folders)} pastas; raiz idFolder={root}")

        # 1) inventário completo de artigos, paginando a pasta raiz (recursivo)
        first = _list_folder(req, root, 1)
        total = first.get("total", 0)
        pages = math.ceil(total / 10) or 1
        print(f"[citsmart] paginando inventário: {pages} páginas (~{total} artigos)", flush=True)
        inventory: dict[int, dict] = {}
        for sp in range(1, pages + 1):
            page_data = first if sp == 1 else _list_folder(req, root, sp)
            for art in page_data.get("content", []):
                aid = art.get("idBaseConhecimento")
                if aid:
                    inventory.setdefault(aid, art)
            if sp % 20 == 0:
                print(f"[citsmart]   página {sp}/{pages} ({len(inventory)} únicos)", flush=True)
        print(f"[citsmart] inventário: {len(inventory)} de ~{total} artigos", flush=True)

        # 2) buscar conteúdo e salvar
        saved = errors = 0
        for aid, art in inventory.items():
            try:
                conteudo = art.get("conteudo")
                titulo = art.get("titulo") or f"Artigo {aid}"
                if not conteudo:
                    full = _event(req, "getKnowledgeById", idKnowledgeBase=aid)
                    conteudo = (full or {}).get("conteudo") or ""
                    titulo = (full or {}).get("titulo") or titulo
                if not conteudo.strip():
                    continue
                folder_path = paths.get(art.get("idPasta"), "")
                doc = catalog.upsert(
                    Document(
                        source=Source.STI_KB,
                        url=f"{PORTAL}#/knowledge/{aid}",
                        title=titulo,
                        orgao=folder_path,
                        content_type="text/html",
                        status=DocStatus.DISCOVERED,
                        extra={"id_kb": aid, "folder": folder_path},
                    )
                )
                (raw_dir / f"{doc.id}.html").write_text(conteudo, encoding="utf-8")
                catalog.record_fetch(doc.id, status=DocStatus.FETCHED, content_type="text/html")
                saved += 1
                if saved % 50 == 0:
                    print(f"[citsmart] {saved}/{len(inventory)} salvos...", flush=True)
                if limit and saved >= limit:
                    break
            except Exception as exc:  # noqa: BLE001
                errors += 1
                print(f"[citsmart] erro artigo {aid}: {type(exc).__name__}: {exc}", flush=True)
        browser.close()
        print(f"[citsmart] FIM: {saved} artigos salvos, {errors} erros")


def main() -> None:
    ap = argparse.ArgumentParser(description="Crawler da Base de Conhecimento do STI (CITSmart)")
    ap.add_argument("--limit", type=int, default=None, help="máximo de artigos (teste)")
    args = ap.parse_args()
    settings = Settings()
    catalog = Catalog(sqlite_path(settings.catalog_dsn))
    run(args.limit, settings.data_dir, catalog)
    catalog.close()


if __name__ == "__main__":
    main()
