"""Crawler do Guia do Estudante e da Comunidade (www.uff.br) — tutoriais para ALUNOS.

O site institucional é WordPress (tema Divi) com a REST API aberta. Coletamos três
tipos de conteúdo how-to voltado ao estudante/comunidade, FILTRANDO por taxonomia o
que é de servidor (RH/gestão de pessoas):

  - faq     : /wp-json/wp/v2/faqs   -> pergunta (title) + resposta (content.rendered), já limpo.
  - servico : /wp-json/wp/v2/servico -> Carta de Serviços (conteúdo montado pelo Divi, então a
              REST vem vazia); salvamos a PÁGINA e o pipeline (trafilatura) extrai o conteúdo.
  - páginas : hub /prograd/formatura-e-diploma/ e afins (mesmo caminho de extração).

Cada item vira ``data/raw/guia/{doc.id}.html`` (FETCHED) e uma linha no catálogo
(``source='guia'``), reaproveitando o pipeline de indexação (parse_any -> HTML). A
categoria/grupo vira o "órgão" (contexto do embedding). O filtro de audiência é por
NOME de taxonomia (exclui grupos de servidor), não por heurística frágil no texto.

    uv run python scripts/crawl_guia.py --limit 20      # slice de teste
    uv run python scripts/crawl_guia.py --only faq      # só um tipo (faq|servico|pages)
    uv run python scripts/crawl_guia.py --force         # re-baixa mesmo o que já existe
"""

from __future__ import annotations

import argparse
import html as _html
import re
import time
from pathlib import Path

import httpx
from uff_core.catalog import Catalog
from uff_core.config import Settings, sqlite_path
from uff_core.schemas import DocStatus, Document, Source

BASE = "https://www.uff.br"
UA = "BaseUFF-crawler/1.0 (UFF academic research; contact marcusantonio@id.uff.br)"

# Páginas editoriais (WP page) que consolidam o tema diploma/formatura para o aluno.
PAGES = [
    f"{BASE}/prograd/formatura-e-diploma/",
]

# Conteúdo de SERVIDOR (RH/gestão/interno), excluído pelo NOME da categoria/grupo da
# taxonomia. Derivado das 15 categorias de `servico` e dos 49 grupos de `faq` (verificado
# ao vivo): estes substrings cobrem TODOS os grupos de servidor sem pegar os do estudante.
SERVIDOR_KW = (
    "gestão de pessoas",
    "aposentadoria",
    "jornada de trabalho",
    "avaliações de desempenho",
    "desempenho durante a pandemia",
    "progepe",
    "decreto 10.139",
    "programa de gestão",
    "movimentação interna",
    "ajuste de lotação",
    "levantamento de necessidades",
    "flexibilização",
    "extensão - docente",
    "extensão-técnico",
    "extensão - técnico",
    "migração de sites",
    "ponto eletrônico",
    "pensão por morte",
    "abono de permanência",
    "tempo especial",
    "comissão de ética",
    "relatório anual do docente",
    "plano de desenvolvimento institucional",
    "diárias e passagens",
    "desenvolvimento de competências",
    "dados cadastrais",
)


def is_servidor(nome: str) -> bool:
    """True se o nome da categoria/grupo indica conteúdo de servidor (a excluir)."""
    n = (nome or "").lower()
    return any(kw in n for kw in SERVIDOR_KW)


def clean_title(rendered: str) -> str:
    """Desescapa entidades e remove o sufixo do site do <title>/title.rendered."""
    t = _html.unescape(re.sub(r"<[^>]+>", "", rendered or "")).strip()
    t = re.split(r"\s*[|–—-]\s*Universidade Federal Fluminense", t)[0].strip()
    return t or "—"


def orgao_de(term_ids: list[int], termos: dict[int, str]) -> str | None:
    """Órgão/contexto = primeira categoria NÃO-servidor do item (senão a primeira)."""
    nomes = [termos.get(t) for t in (term_ids or []) if termos.get(t)]
    if not nomes:
        return None
    for n in nomes:
        if not is_servidor(n):
            return n
    return nomes[0]


def keep_item(term_ids: list[int], termos: dict[int, str]) -> bool:
    """Mantém o item a menos que TODAS as suas categorias sejam de servidor."""
    nomes = [termos.get(t) for t in (term_ids or []) if termos.get(t)]
    if not nomes:
        return True  # sem categoria: inclui (default abrangente para 'comunidade')
    return not all(is_servidor(n) for n in nomes)


def faq_fragment(title: str, content_html: str) -> str:
    """HTML mínimo (pergunta + resposta) que o trafilatura extrai limpo no pipeline."""
    return (
        "<!doctype html><html lang='pt-br'><head><meta charset='utf-8'>"
        f"<title>{_html.escape(title)}</title></head><body><article>"
        f"<h1>{_html.escape(title)}</h1>{content_html}</article></body></html>"
    )


def page_title(html: str, fallback: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    return clean_title(m.group(1)) if m else fallback


class Rest:
    def __init__(self, client: httpx.Client, pause: float) -> None:
        self.c = client
        self.pause = pause

    def get(self, url: str, **kw) -> httpx.Response | None:
        """GET com retries. Retorna a resposta 200, ou None se falhar de vez (o chamador trata)."""
        last: httpx.Response | None = None
        for attempt in range(4):
            try:
                last = self.c.get(url, **kw)
                if last.status_code == 200:
                    time.sleep(self.pause)
                    return last
            except httpx.HTTPError:
                last = None
            time.sleep(1.5 * (attempt + 1))
        return last  # pode ser None ou uma resposta != 200

    def terms(self, tax_base: str) -> dict[int, str]:
        out: dict[int, str] = {}
        r = self.get(f"{BASE}/wp-json/wp/v2/{tax_base}", params={"per_page": 100})
        if r is not None and r.status_code == 200:
            for t in r.json():
                out[t["id"]] = t.get("name") or ""
        return out

    def all(self, base: str):
        """Itera todos os posts de um tipo, paginando (per_page=100)."""
        page = 1
        while True:
            r = self.get(
                f"{BASE}/wp-json/wp/v2/{base}",
                params={"per_page": 100, "page": page, "orderby": "title", "order": "asc"},
            )
            if r is None or r.status_code != 200:
                break
            items = r.json()
            if not items:
                break
            yield from items
            total_pages = int(r.headers.get("X-WP-TotalPages", "1") or 1)
            if page >= total_pages:
                break
            page += 1


def already_have(catalog: Catalog, raw_dir: Path, url: str, force: bool) -> bool:
    """True se o doc já foi baixado (para pular ANTES de refazer o fetch, no resume)."""
    if force:
        return False
    existing = catalog.get_by_url(Source.GUIA, url)
    return bool(
        existing
        and existing.status in (DocStatus.FETCHED, DocStatus.INDEXED)
        and (raw_dir / f"{existing.id}.html").exists()
    )


def _save(
    catalog: Catalog,
    raw_dir: Path,
    *,
    url: str,
    title: str,
    orgao: str | None,
    html: str,
    tipo: str,
    force: bool,
) -> str:
    """Registra o doc (GUIA) e grava o HTML em raw/guia/{id}.html. Retorna 'saved'|'skip'."""
    if already_have(catalog, raw_dir, url, force):
        return "skip"
    doc = catalog.upsert(
        Document(
            source=Source.GUIA,
            url=url,
            title=title,
            orgao=orgao,
            content_type="text/html",
            status=DocStatus.DISCOVERED,
            extra={"tipo": tipo, "categoria": orgao},
        )
    )
    (raw_dir / f"{doc.id}.html").write_text(html, encoding="utf-8")
    catalog.record_fetch(doc.id, status=DocStatus.FETCHED, content_type="text/html")
    return "saved"


def crawl_faq(rest: Rest, catalog: Catalog, raw_dir: Path, limit: int | None, force: bool) -> None:
    termos = rest.terms("faq_groups")
    saved = skip = drop = 0
    for it in rest.all("faqs"):
        cats = it.get("faq_groups", [])
        if not keep_item(cats, termos):
            drop += 1
            continue
        title = clean_title(it["title"]["rendered"])
        content = (it.get("content") or {}).get("rendered") or ""
        if not content.strip():
            drop += 1
            continue
        r = _save(
            catalog, raw_dir,
            url=it["link"], title=title, orgao=orgao_de(cats, termos),
            html=faq_fragment(title, content), tipo="faq", force=force,
        )
        saved += r == "saved"
        skip += r == "skip"
        if (saved + skip) % 50 == 0:
            print(f"[guia/faq] {saved} salvos, {skip} pulados, {drop} de servidor...", flush=True)
        if limit and saved >= limit:
            break
    print(f"[guia/faq] FIM: {saved} salvos, {skip} já existentes, {drop} excluídos (servidor)")


def crawl_servico(
    rest: Rest, catalog: Catalog, raw_dir: Path, limit: int | None, force: bool
) -> None:
    termos = rest.terms("categoria-de-servico")
    saved = skip = drop = err = 0
    for it in rest.all("servico"):
        cats = it.get("categoria-de-servico", [])
        if not keep_item(cats, termos):
            drop += 1
            continue
        link = it["link"]
        if already_have(catalog, raw_dir, link, force):  # resume: não re-baixa a página
            skip += 1
            continue
        page = rest.get(link)
        if page is None or page.status_code != 200:
            err += 1
            continue
        title = clean_title(it["title"]["rendered"])
        r = _save(
            catalog, raw_dir,
            url=link, title=title, orgao=orgao_de(cats, termos),
            html=page.text, tipo="servico", force=force,
        )
        saved += r == "saved"
        skip += r == "skip"
        if (saved + skip) % 25 == 0:
            print(f"[guia/servico] {saved} salvos, {skip} pulados, {drop} servidor...", flush=True)
        if limit and saved >= limit:
            break
    print(
        f"[guia/servico] FIM: {saved} salvos, {skip} já existentes, "
        f"{drop} excluídos (servidor), {err} erros"
    )


def crawl_pages(rest: Rest, catalog: Catalog, raw_dir: Path, force: bool) -> None:
    saved = 0
    for url in PAGES:
        r = rest.get(url)
        if r is None or r.status_code != 200:
            print(f"[guia/pages] erro em {url}")
            continue
        title = page_title(r.text, url.rstrip("/").rsplit("/", 1)[-1])
        res = _save(
            catalog, raw_dir,
            url=url, title=title, orgao="Diploma e Formatura", html=r.text,
            tipo="page", force=force,
        )
        saved += res == "saved"
    print(f"[guia/pages] FIM: {saved} páginas salvas")


def main() -> None:
    ap = argparse.ArgumentParser(description="Crawler do Guia do Estudante (www.uff.br)")
    ap.add_argument("--limit", type=int, default=None, help="máx. de itens por tipo (teste)")
    ap.add_argument("--only", choices=["faq", "servico", "pages"], help="só um tipo")
    ap.add_argument("--force", action="store_true", help="re-baixa mesmo o que já existe")
    ap.add_argument("--pause", type=float, default=0.4, help="pausa entre requisições (s)")
    args = ap.parse_args()

    settings = Settings()
    catalog = Catalog(sqlite_path(settings.catalog_dsn))
    raw_dir = Path(settings.data_dir) / "raw" / Source.GUIA.value
    raw_dir.mkdir(parents=True, exist_ok=True)

    with httpx.Client(
        timeout=30, headers={"User-Agent": UA}, follow_redirects=True
    ) as client:
        rest = Rest(client, args.pause)
        if args.only in (None, "pages"):
            crawl_pages(rest, catalog, raw_dir, args.force)
        if args.only in (None, "servico"):
            crawl_servico(rest, catalog, raw_dir, args.limit, args.force)
        if args.only in (None, "faq"):
            crawl_faq(rest, catalog, raw_dir, args.limit, args.force)
    catalog.close()


if __name__ == "__main__":
    main()
