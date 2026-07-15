"""Crawler da 78ª Reunião Anual da SBPC (UFF, Niterói, 26/07–01/08/2026) + SBPC institucional.

Coleta TUDO sobre a edição e sobre a própria SBPC, em sete coletores (``--only``):

  - programacao : reunioes2.sbpcnet.org.br/programacao/ — 1 doc POR ATIVIDADE (mesa-redonda,
                  conferência, sessão especial…), com dia/horário/modalidade/local/pessoas.
                  ``publish_date`` = DIA da atividade (habilita filtro por dia do evento).
  - minicursos  : reunioes2.sbpcnet.org.br/programacao/mc/ — 1 doc por (web)minicurso, com
                  código, ministrantes, ementa e público-alvo.
  - pages78     : site oficial da edição (ra.sbpcnet.org.br/78RA, WordPress com REST aberta).
  - uffsbpc     : site da UFF para o evento (sbpc.uff.br — saúde, cultural, jovem).
  - portal      : páginas institucionais da SBPC (portal.sbpcnet.org.br: história, estatuto…).
  - noticias    : a listagem de notícias da 78RA agrega LINKS EXTERNOS (Jornal da Ciência,
                  www.uff.br); seguimos cada link e salvamos a matéria.
  - pdfs        : caderno de pôsteres (660+ trabalhos), programações temáticas e normas.

Atividades/minicursos viram fragmentos HTML sintetizados (padrão do crawl_guia) que o
trafilatura extrai limpo no pipeline; páginas e notícias são salvas cruas. Como a programação
MUDA até o evento, ``_save`` compara checksum e, se um doc já INDEXED mudou, purga seus points
no Qdrant e rebaixa para FETCHED — senão o ``run_batch`` pularia o doc (idempotência pelo 1º
chunk) e o índice ficaria desatualizado.

ATENÇÃO TLS: ra.sbpcnet.org.br e reunioes2.sbpcnet.org.br servem cadeia de certificado
incompleta (falta o intermediário); só para esses hosts usamos um client ``verify=False``.

    uv run python scripts/crawl_sbpc.py --limit 5 --only programacao   # slice de teste
    uv run python scripts/crawl_sbpc.py                                # coleta completa
    uv run python scripts/crawl_sbpc.py --force                        # re-baixa tudo
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html as _html
import re
import time
import unicodedata
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

import httpx
from selectolax.parser import HTMLParser
from uff_core.catalog import Catalog
from uff_core.config import Settings, sqlite_path
from uff_core.schemas import DocStatus, Document, Source

BASE_RA = "https://ra.sbpcnet.org.br/78RA"
BASE_PROG = "https://reunioes2.sbpcnet.org.br"
BASE_PORTAL = "https://portal.sbpcnet.org.br"
BASE_UFF_SBPC = "https://sbpc.uff.br"
UA = "BaseUFF-crawler/1.0 (UFF academic research; contact marcusantonio@id.uff.br)"

# Hosts com cadeia TLS incompleta (falta o certificado intermediário): só nesses dois o
# client é verify=False; todos os demais usam verificação normal.
HOSTS_TLS_QUEBRADO = ("ra.sbpcnet.org.br", "reunioes2.sbpcnet.org.br")

EVENTO_NOTA = (
    "78ª Reunião Anual da SBPC — UFF, Campus Gragoatá, Niterói/RJ, "
    "26 de julho a 1º de agosto de 2026"
)

# Páginas institucionais da SBPC (portal sem REST aberta — crawl HTML de lista curada).
PORTAL_PAGES = [
    f"{BASE_PORTAL}/a-sbpc/quem-somos/",
    f"{BASE_PORTAL}/a-sbpc/missao-visao-e-valores/",
    f"{BASE_PORTAL}/a-sbpc/historico/historia/",
    f"{BASE_PORTAL}/a-sbpc/historico/cientistas-homenageados/",
    f"{BASE_PORTAL}/a-sbpc/historico/presidentes-de-honra/",
    f"{BASE_PORTAL}/a-sbpc/historico/diretorias-anteriores/",
    f"{BASE_PORTAL}/a-sbpc/estatuto-e-regimento/",
    f"{BASE_PORTAL}/a-sbpc/gestao/diretoria/",
    f"{BASE_PORTAL}/a-sbpc/gestao/conselho/",
    f"{BASE_PORTAL}/a-sbpc/gestao/regionais/",
    f"{BASE_PORTAL}/70-reunioes-anuais-da-sbpc/",
    f"{BASE_PORTAL}/apresentacao/",
    f"{BASE_PORTAL}/associadas/",
    f"{BASE_PORTAL}/eventos/78a-reuniao-anual-da-sbpc/",
]

# (url, título, tipo) — PDFs oficiais da edição.
PDFS = [
    (
        f"{BASE_RA}/wp-content/uploads/2026/07/78ra_posteres.pdf",
        "Caderno de pôsteres da 78ª RA — trabalhos aprovados (Sessão de Pôsteres/JNIC)",
        "poster",
    ),
    (
        f"{BASE_RA}/wp-content/uploads/2026/07/"
        "Programa%C3%A7%C3%A3o_SBPC_G%C3%AAnero_Completa_final.pdf",
        "Programação SBPC Gênero — 78ª RA (completa)",
        "programacao-tematica",
    ),
    (
        f"{BASE_RA}/wp-content/uploads/2026/07/SBPC-PROGRAMA%C3%87%C3%83O-AFRO-IND%C3%8DGENA.pdf",
        "Programação SBPC Afro e Indígena — 78ª RA",
        "programacao-tematica",
    ),
    (
        f"{BASE_RA}/wp-content/uploads/2025/12/5_Normas_de_minicursos_78RA.pdf",
        "Normas de minicursos da 78ª RA",
        "documento",
    ),
]

# Prefixo do título -> slug do tipo de atividade (chaves já "foldadas": sem acento, minúsculas).
TIPOS = {
    "mesa-redonda": "mesa-redonda",
    "mesa redonda": "mesa-redonda",
    "conferencia": "conferencia",
    "sessao especial": "sessao-especial",
    "encontro": "encontro",
    "assembleia": "assembleia",
    "oficina": "oficina",
    "minicurso": "minicurso",
    "painel": "painel",
    "simposio": "simposio",
    "reuniao": "reuniao",
    "sessao de posteres": "poster",
}
TIPO_LEGIVEL = {
    "mesa-redonda": "Mesa-Redonda",
    "conferencia": "Conferência",
    "sessao-especial": "Sessão Especial",
    "encontro": "Encontro",
    "assembleia": "Assembleia",
    "oficina": "Oficina",
    "minicurso": "Minicurso",
    "webminicurso": "Webminicurso",
    "painel": "Painel",
    "simposio": "Simpósio",
    "reuniao": "Reunião",
    "poster": "Pôster",
    "atividade": "Atividade",
}

# Rótulos de pessoas nas células da programação ("Coordenadora:", "Palestrantes:", …).
_PAPEIS = {
    "coordenador",
    "coordenadora",
    "coordenadores",
    "coordenadoras",
    "palestrante",
    "palestrantes",
    "conferencista",
    "conferencistas",
    "participante",
    "participantes",
    "apresentador",
    "apresentadora",
    "apresentadores",
    "ministrante",
    "ministrantes",
    "proponente",
    "proponentes",
    "debatedor",
    "debatedora",
    "debatedores",
    "mediador",
    "mediadora",
    "mediadores",
    "moderador",
    "moderadora",
}

_TRILHA_RE = re.compile(r"\(\s*(SBPC[^)]{0,60})\)\s*$")
_ROTULO_RE = re.compile(r"^([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ \-]{2,29})\s*:\s*(.+)$", re.S)
_DATA_RE = re.compile(
    r"^((?:segunda|ter[çc]a|quarta|quinta|sexta|s[áa]bado|domingo)(?:-feira)?)\s*,\s*"
    r"(\d{1,2})/(\d{1,2})/(\d{4})\s*[-–]?\s*(.*)$",
    re.I | re.S,
)
_PERIODO_RE = re.compile(r"^De\s+(\d{1,2})/(\d{1,2})/(\d{4})", re.I)
_FAIXA_RE = re.compile(r"das\s+(\d{1,2})h(\d{2})?\s*[àa]s?\s+(\d{1,2})h(\d{2})?", re.I)
_HORA_RE = re.compile(r"[àa]s?\s+(\d{1,2})h(\d{2})?", re.I)
_MC_COD_RE = re.compile(r"^(W?MC)\s*-?\s*(\d+)\s*[-–—]\s*(.+)$", re.S)
_SUFIXO_TITULO_RE = re.compile(
    r"\s*[|:–—-]\s*(78ª Reuni[ãa]o Anual da SBPC|SBPC(\s*[-–—].*)?|Portal SBPC)\s*$", re.I
)


# -- funções puras (testadas em packages/ingest/tests/test_sbpc_parse.py) -------------------


def texto(s: str | None) -> str:
    """Desescapa entidades, normaliza nbsp e colapsa espaços."""
    return re.sub(r"\s+", " ", _html.unescape(s or "").replace("\xa0", " ")).strip()


def _fold(s: str | None) -> str:
    """Minúsculas sem acento (comparação tolerante de rótulos/tipos)."""
    norm = unicodedata.normalize("NFKD", (s or "").lower())
    return "".join(c for c in norm if not unicodedata.combining(c)).strip()


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _fold(s)).strip("-")[:80].rstrip("-")


def clean_title(rendered: str) -> str:
    """Título limpo a partir de ``title.rendered``/``<title>`` (remove sufixo do site)."""
    t = texto(re.sub(r"<[^>]+>", "", rendered or ""))
    return _SUFIXO_TITULO_RE.sub("", t).strip() or "—"


def page_title(html_doc: str, fallback: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html_doc, re.S | re.I)
    return clean_title(m.group(1)) if m else fallback


def rest_date(s: str | None) -> dt.date | None:
    """``2026-07-10T18:22:33`` (REST WP) -> date."""
    try:
        return dt.date.fromisoformat((s or "")[:10])
    except ValueError:
        return None


def meta_publish_date(html_doc: str) -> dt.date | None:
    """Data de ``<meta property='article:published_time'>`` (notícias WP)."""
    node = HTMLParser(html_doc).css_first('meta[property="article:published_time"]')
    if node is None:
        return None
    return rest_date(node.attributes.get("content"))


def split_tipo_titulo(bruto: str) -> tuple[str, str, str | None]:
    """``"Mesa-Redonda: X (SBPC Gênero)"`` -> ("mesa-redonda", "X", "SBPC Gênero")."""
    t = texto(bruto)
    trilha = None
    m = _TRILHA_RE.search(t)
    if m:
        trilha = m.group(1).strip()
        t = t[: m.start()].strip()
    if ":" in t:
        prefixo, resto = t.split(":", 1)
        tipo = TIPOS.get(_fold(prefixo))
        if tipo:
            return tipo, resto.strip(), trilha
    return "atividade", t, trilha


def _hora(h: str, m: str | None) -> str:
    return f"{int(h)}h{m or '00'}"


def _horario_de(trecho: str) -> str | None:
    m = _FAIXA_RE.search(trecho)
    if m:
        return f"{_hora(m.group(1), m.group(2))} às {_hora(m.group(3), m.group(4))}"
    m = _HORA_RE.search(trecho)
    if m:
        return _hora(m.group(1), m.group(2))
    return None


def parse_data_horario(linha: str) -> dict | None:
    """``"Quarta-feira, 29/7/2026 - das 13h00 às 15h30"`` -> dia/dia_semana/horario.

    Tolera minutos ausentes ("às 9h"), hora única ("às 17h30") e o formato de período
    das oficinas ("De 31/7/2026 à …"). Retorna None se a linha não for de data.
    """
    t = texto(linha)
    m = _DATA_RE.match(t)
    if m:
        try:
            dia = dt.date(int(m.group(4)), int(m.group(3)), int(m.group(2)))
        except ValueError:
            return None
        return {"dia": dia, "dia_semana": m.group(1).lower(), "horario": _horario_de(m.group(5))}
    m = _PERIODO_RE.match(t)
    if m:
        try:
            dia = dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
        return {"dia": dia, "dia_semana": None, "horario": _horario_de(t), "periodo": t}
    return None


def parse_pessoas(valor: str) -> list[str]:
    """``"Ana Silva (UFF), João (USP) e Bia (ALERJ)"`` -> lista de "Nome (SIGLA)"."""
    v = texto(valor).rstrip(".")
    if not v:
        return []
    if ")" in v:
        partes = re.split(r"\)\s*(?:,|;|\s+e\s+)\s*", v)
        return [p if p.endswith(")") else p + ")" for p in (x.strip() for x in partes) if p]
    return [p.strip() for p in re.split(r"\s*(?:,|;|\s+e\s+)\s*", v) if p.strip()]


def parse_programacao(html_doc: str) -> list[dict]:
    """Programação científica: 1 dict por bloco ``<table>`` de atividade."""
    atividades: list[dict] = []
    for table in HTMLParser(html_doc).css("table"):
        linhas: list[tuple[str, bool]] = []
        for tr in table.css("tr"):
            txt = texto(tr.text(deep=True))
            if txt:  # células vazias (<td height=15>) são separadores
                linhas.append((txt, tr.css_first("em") is not None))
        if not linhas:
            continue
        tipo, titulo, trilha = split_tipo_titulo(linhas[0][0])
        a: dict = {
            "titulo_bruto": texto(linhas[0][0]),
            "tipo": tipo,
            "titulo": titulo,
            "trilha": trilha,
            "dia": None,
            "dia_semana": None,
            "horario": None,
            "periodo": None,
            "modalidade": None,
            "local": None,
            "publico_alvo": None,
            "ementa": None,
            "pessoas": {},
        }
        for txt, tem_em in linhas[1:]:
            quando = parse_data_horario(txt)
            if quando:
                a.update(quando)
                continue
            m = _ROTULO_RE.match(txt)
            if m:
                rotulo, valor = m.group(1).strip(), m.group(2).strip()
                chave = _fold(rotulo)
                if chave == "modalidade":
                    a["modalidade"] = valor
                elif chave == "ementa":
                    a["ementa"] = valor
                elif chave in ("publico-alvo", "publico alvo"):
                    a["publico_alvo"] = valor
                elif chave in _PAPEIS:
                    a["pessoas"][rotulo] = parse_pessoas(valor)
                    continue
                continue
            if tem_em and not a["local"]:
                a["local"] = txt
        if a["dia"] or a["pessoas"] or a["modalidade"]:  # descarta tabela que não é atividade
            atividades.append(a)
    return atividades


def coordenador_de(a: dict) -> str | None:
    nomes = [
        n for rot, ns in a["pessoas"].items() if _fold(rot).startswith("coordenador") for n in ns
    ]
    return "; ".join(nomes) or None


def palestrantes_de(a: dict) -> list[str]:
    return [
        n
        for rot, ns in a["pessoas"].items()
        if not _fold(rot).startswith("coordenador")
        for n in ns
    ]


def atividade_url(a: dict) -> str:
    """URL única por atividade (chave (source, url) do catálogo): dia + título + hora."""
    partes = [
        a["dia"].isoformat() if a["dia"] else None,
        slugify(a["titulo"]),
        slugify(a["horario"] or "") or None,
    ]
    return f"{BASE_PROG}/programacao/#" + "-".join(p for p in partes if p)


def _linha(rotulo: str, valor: str | None) -> str:
    return f"<p><b>{_html.escape(rotulo)}:</b> {_html.escape(valor)}</p>" if valor else ""


def _envelope(title: str, body_html: str) -> str:
    """HTML mínimo que o trafilatura extrai limpo no pipeline (padrão faq_fragment)."""
    return (
        "<!doctype html><html lang='pt-br'><head><meta charset='utf-8'>"
        f"<title>{_html.escape(title)}</title></head><body><article>"
        f"<h1>{_html.escape(title)}</h1>{body_html}</article></body></html>"
    )


def atividade_fragment(a: dict) -> str:
    quando = None
    if a["dia"]:
        quando = ", ".join(
            p
            for p in (
                a["dia_semana"],
                a["dia"].strftime("%d/%m/%Y"),
                f"das {a['horario']}" if a["horario"] and " às " in a["horario"] else a["horario"],
            )
            if p
        )
    corpo = _linha("Tipo", TIPO_LEGIVEL.get(a["tipo"], a["tipo"]))
    corpo += _linha("Quando", quando)
    corpo += _linha("Modalidade", a["modalidade"])
    corpo += _linha("Local", a["local"])
    for rotulo, nomes in a["pessoas"].items():
        corpo += _linha(rotulo, "; ".join(nomes))
    corpo += _linha("Programa", a["trilha"])
    corpo += _linha("Ementa", a["ementa"])
    corpo += _linha("Público-alvo", a["publico_alvo"])
    corpo += _linha("Evento", EVENTO_NOTA)
    return _envelope(a["titulo_bruto"], corpo)


def tipo_minicurso(item: dict) -> str:
    """ "minicurso" (presencial) ou "webminicurso" (virtual, código WMC-…)."""
    web = (item.get("codigo") or "").upper().startswith("WMC")
    return "webminicurso" if web or "web" in _fold(item.get("secao")) else "minicurso"


def parse_minicursos(html_doc: str) -> list[dict]:
    """Minicursos/webminicursos: 1 dict por ``div.minicurso-item`` (seção via <h4>)."""
    secao = None
    itens: list[dict] = []
    # travessia em ordem de documento (css("h4, div.x") agrupa por seletor, não por posição)
    for node in HTMLParser(html_doc).root.traverse(include_text=False):
        if node.tag == "h4":
            secao = texto(node.text(deep=True))
            continue
        if node.tag != "div" or "minicurso-item" not in (node.attributes.get("class") or ""):
            continue
        ps = node.css("p")
        if not ps:
            continue
        bruto = texto(ps[0].text(deep=True))
        m = _MC_COD_RE.match(bruto)
        codigo, titulo = (f"{m.group(1)}-{m.group(2)}", m.group(3).strip()) if m else (None, bruto)
        campos: dict[str, str] = {}
        for p in ps[1:]:
            mm = _ROTULO_RE.match(texto(p.text(deep=True)))
            if mm:
                campos[_fold(mm.group(1))] = mm.group(2).strip()
        itens.append({"codigo": codigo, "titulo": titulo, "secao": secao, "campos": campos})
    return itens


def minicurso_fragment(item: dict) -> str:
    campos = item["campos"]
    titulo = f"{item['codigo']} — {item['titulo']}" if item["codigo"] else item["titulo"]
    corpo = _linha("Tipo", TIPO_LEGIVEL[tipo_minicurso(item)])
    corpo += _linha("Seção", item["secao"])
    corpo += _linha("Ministrantes", campos.get("ministrantes") or campos.get("ministrante"))
    corpo += _linha("Ementa", campos.get("ementa"))
    corpo += _linha("Público-alvo", campos.get("publico-alvo") or campos.get("publico alvo"))
    corpo += _linha("Local", campos.get("local"))
    corpo += _linha("Prazo para assistir", campos.get("prazo para assistir"))
    corpo += _linha("Evento", EVENTO_NOTA)
    return _envelope(titulo, corpo)


def links_noticias(html_doc: str) -> list[dict]:
    """Itens da listagem de notícias da 78RA: url (externa), título e data, dedup por url."""
    itens: list[dict] = []
    vistos: set[str] = set()
    for art in HTMLParser(html_doc).css("article.noticia, article.type-noticia"):
        a = art.css_first("h2.entry-title a") or art.css_first("h2 a")
        if a is None:
            continue
        url = (a.attributes.get("href") or "").strip()
        if not url or url in vistos:
            continue
        vistos.add(url)
        data = None
        meta = art.css_first(".entry-meta")
        if meta:
            m = re.search(r"(\d{2})/(\d{2})/(\d{4})", meta.text())
            if m:
                data = dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        itens.append({"url": url, "titulo": texto(a.text(deep=True)), "data": data})
    return itens


def veiculo_de(url: str) -> str:
    host = urlparse(url).netloc
    if "jornaldaciencia" in host:
        return "Jornal da Ciência"
    if host.endswith("uff.br"):
        return "UFF"
    if "sbpcnet" in host:
        return "SBPC"
    return host


def pagina_fragment(title: str, content_html: str) -> str:
    return _envelope(title, content_html)


# -- catálogo + Qdrant -----------------------------------------------------------------------


def make_purge(qdrant_url: str, collection: str) -> Callable[[int], None]:
    """Remove os points de um doc no Qdrant (necessário quando o conteúdo MUDA).

    Sem isso o ``run_batch`` pularia o doc (idempotência pelo 1º chunk) e o índice ficaria
    com a versão antiga da programação. Qdrant fora do ar não pode abortar o crawl.
    """

    def purge(doc_id: int) -> None:
        try:
            from qdrant_client import QdrantClient, models

            cli = QdrantClient(url=qdrant_url, timeout=30)
            cli.delete(
                collection,
                points_selector=models.FilterSelector(
                    filter=models.Filter(
                        must=[
                            models.FieldCondition(
                                key="doc_id", match=models.MatchValue(value=doc_id)
                            )
                        ]
                    )
                ),
            )
        except Exception as exc:  # noqa: BLE001 — crawl segue mesmo com Qdrant indisponível
            print(f"[sbpc] aviso: purge do doc {doc_id} falhou: {exc}", flush=True)

    return purge


def _save(
    catalog: Catalog,
    raw_dir: Path,
    purge: Callable[[int], None],
    *,
    url: str,
    title: str,
    orgao: str | None,
    content: str | bytes,
    tipo: str,
    publish_date: dt.date | None = None,
    extra: dict | None = None,
    suffix: str = ".html",
    content_type: str = "text/html",
    force: bool = False,
) -> str:
    """Registra o doc e grava raw/sbpc/{id}{suffix}. Retorna 'saved'|'skip'|'updated'.

    'skip'    = checksum idêntico ao já catalogado (nada a fazer; INDEXED continua INDEXED).
    'updated' = doc já estava INDEXED e o conteúdo mudou -> purga points e volta a FETCHED.
    """
    dados = content.encode("utf-8") if isinstance(content, str) else content
    checksum = hashlib.sha256(dados).hexdigest()
    existente = catalog.get_by_url(Source.SBPC, url)
    if (
        existente
        and not force
        and existente.checksum == checksum
        and existente.status in (DocStatus.FETCHED, DocStatus.INDEXED)
        and (raw_dir / f"{existente.id}{suffix}").exists()
    ):
        return "skip"
    mudou = bool(
        existente
        and existente.status == DocStatus.INDEXED
        and existente.checksum
        and existente.checksum != checksum
    )
    doc = catalog.upsert(
        Document(
            source=Source.SBPC,
            url=url,
            title=title,
            orgao=orgao,
            publish_date=publish_date,
            content_type=content_type,
            checksum=checksum,
            status=DocStatus.DISCOVERED,
            extra={"tipo": tipo, **(extra or {})},
        )
    )
    (raw_dir / f"{doc.id}{suffix}").write_bytes(dados)
    if mudou:
        purge(doc.id)
    catalog.record_fetch(
        doc.id, status=DocStatus.FETCHED, content_type=content_type, checksum=checksum
    )
    return "updated" if mudou else "saved"


def ja_baixado(catalog: Catalog, raw_dir: Path, url: str, force: bool) -> bool:
    """True se já temos o doc (pula ANTES de re-baixar conteúdo que não muda, ex. notícia)."""
    if force:
        return False
    d = catalog.get_by_url(Source.SBPC, url)
    return bool(
        d and d.status in (DocStatus.FETCHED, DocStatus.INDEXED) and any(raw_dir.glob(f"{d.id}.*"))
    )


# -- HTTP ------------------------------------------------------------------------------------


class Http:
    """GET com retries/backoff + iteração paginada da REST do WordPress."""

    def __init__(self, client: httpx.Client, pause: float) -> None:
        self.c = client
        self.pause = pause

    def get(self, url: str, retries: int = 4, **kw) -> httpx.Response | None:
        last: httpx.Response | None = None
        for attempt in range(retries):
            try:
                last = self.c.get(url, **kw)
                if last.status_code == 200:
                    time.sleep(self.pause)
                    return last
            except httpx.HTTPError:
                last = None
            time.sleep(1.5 * (attempt + 1))
        return last  # pode ser None ou uma resposta != 200 (o chamador trata)

    def wp_all(self, site: str, tipo: str = "pages"):
        """Itera todos os itens de ``{site}/wp-json/wp/v2/{tipo}`` (per_page=100)."""
        page = 1
        while True:
            r = self.get(
                f"{site}/wp-json/wp/v2/{tipo}",
                retries=2,
                params={"per_page": 100, "page": page},
            )
            if r is None or r.status_code != 200:
                break
            items = r.json()
            if not items:
                break
            yield from items
            if page >= int(r.headers.get("X-WP-TotalPages", "1") or 1):
                break
            page += 1


# -- coletores ---------------------------------------------------------------------------------


def crawl_programacao(http_i, catalog, raw_dir, purge, limit, force) -> None:
    r = http_i.get(f"{BASE_PROG}/programacao/")
    if r is None or r.status_code != 200:
        print("[sbpc/programacao] erro ao baixar a página de programação")
        return
    saved = skip = upd = 0
    for a in parse_programacao(r.text)[: limit or None]:
        res = _save(
            catalog,
            raw_dir,
            purge,
            url=atividade_url(a),
            title=a["titulo"],
            orgao=f"78ª RA — {TIPO_LEGIVEL.get(a['tipo'], 'Atividade')}",
            publish_date=a["dia"],
            content=atividade_fragment(a),
            tipo=a["tipo"],
            extra={
                "horario": a["horario"],
                "dia_semana": a["dia_semana"],
                "modalidade": a["modalidade"],
                "local": a["local"],
                "coordenador": coordenador_de(a),
                "palestrantes": palestrantes_de(a),
                "pessoas": a["pessoas"],
                "trilha": a["trilha"],
            },
            force=force,
        )
        saved += res == "saved"
        skip += res == "skip"
        upd += res == "updated"
    print(f"[sbpc/programacao] FIM: {saved} novas, {upd} atualizadas, {skip} inalteradas")


def crawl_minicursos(http_i, catalog, raw_dir, purge, limit, force) -> None:
    r = http_i.get(f"{BASE_PROG}/programacao/mc/")
    if r is None or r.status_code != 200:
        print("[sbpc/minicursos] erro ao baixar a página de minicursos")
        return
    saved = skip = upd = 0
    for item in parse_minicursos(r.text)[: limit or None]:
        campos = item["campos"]
        ancora = item["codigo"] or slugify(item["titulo"])
        ministrantes = campos.get("ministrantes") or campos.get("ministrante") or ""
        res = _save(
            catalog,
            raw_dir,
            purge,
            url=f"{BASE_PROG}/programacao/mc/#{ancora}",
            title=item["titulo"],
            orgao=f"78ª RA — {TIPO_LEGIVEL[tipo_minicurso(item)]}",
            content=minicurso_fragment(item),
            tipo=tipo_minicurso(item),
            extra={
                "codigo": item["codigo"],
                "secao": item["secao"],
                "ministrantes": parse_pessoas(ministrantes),
                "publico_alvo": campos.get("publico-alvo") or campos.get("publico alvo"),
                "local": campos.get("local"),
                "prazo": campos.get("prazo para assistir"),
            },
            force=force,
        )
        saved += res == "saved"
        skip += res == "skip"
        upd += res == "updated"
    print(f"[sbpc/minicursos] FIM: {saved} novos, {upd} atualizados, {skip} inalterados")


def _texto_visivel(html_fragment: str) -> str:
    return re.sub(r"<[^>]+>", " ", html_fragment or "").strip()


def crawl_wp_site(
    http, catalog, raw_dir, purge, *, site: str, orgao: str, limit, force, tipos=("pages",)
) -> None:
    """Páginas (e posts) de um WordPress com REST aberta (site da 78RA e sbpc.uff.br)."""
    nome = urlparse(site).netloc
    saved = skip = upd = err = 0
    for tipo_wp in tipos:
        for it in http.wp_all(site, tipo_wp):
            slug = it.get("slug") or ""
            if "old" in slug:
                continue
            link = it.get("link") or ""
            title = clean_title((it.get("title") or {}).get("rendered") or slug)
            content = (it.get("content") or {}).get("rendered") or ""
            if len(_texto_visivel(content)) >= 200:
                html_doc = pagina_fragment(title, content)
            else:
                # Página montada por page-builder (REST vazia): salva a página renderizada.
                if ja_baixado(catalog, raw_dir, link, force):
                    skip += 1
                    continue
                page = http.get(link)
                if page is None or page.status_code != 200:
                    err += 1
                    continue
                html_doc = page.text
            res = _save(
                catalog,
                raw_dir,
                purge,
                url=link,
                title=title,
                orgao=orgao,
                publish_date=rest_date(it.get("date")),
                content=html_doc,
                tipo="pagina",
                extra={"slug": slug, "site": nome},
                force=force,
            )
            saved += res == "saved"
            skip += res == "skip"
            upd += res == "updated"
            if limit and saved >= limit:
                break
    print(f"[sbpc/{nome}] FIM: {saved} novas, {upd} atualizadas, {skip} inalteradas, {err} erros")


def crawl_portal(http_s, catalog, raw_dir, purge, force) -> None:
    """Páginas institucionais da SBPC (lista curada; portal sem REST aberta)."""
    saved = skip = upd = err = 0
    for url in PORTAL_PAGES:
        r = http_s.get(url)
        if r is None or r.status_code != 200:
            print(f"[sbpc/portal] erro em {url}")
            err += 1
            continue
        res = _save(
            catalog,
            raw_dir,
            purge,
            url=url,
            title=page_title(r.text, url.rstrip("/").rsplit("/", 1)[-1]),
            orgao="SBPC Nacional",
            publish_date=meta_publish_date(r.text),
            content=r.text,
            tipo="institucional",
            force=force,
        )
        saved += res == "saved"
        skip += res == "skip"
        upd += res == "updated"
    print(f"[sbpc/portal] FIM: {saved} novas, {upd} atualizadas, {skip} inalteradas, {err} erros")


def crawl_noticias(http_s, http_i, catalog, raw_dir, purge, limit, force) -> None:
    """Segue os links externos da listagem de notícias da 78RA e salva cada matéria."""
    saved = skip = err = pagina = 0
    while True:
        pagina += 1
        url_lista = f"{BASE_RA}/noticias/" if pagina == 1 else f"{BASE_RA}/noticias/page/{pagina}/"
        r = http_i.get(url_lista, retries=2)
        if r is None or r.status_code != 200:
            break
        itens = links_noticias(r.text)
        if not itens:
            break
        for it in itens:
            if ja_baixado(catalog, raw_dir, it["url"], force):
                skip += 1
                continue
            http = http_i if urlparse(it["url"]).netloc in HOSTS_TLS_QUEBRADO else http_s
            art = http.get(it["url"], retries=2)
            if art is None or art.status_code != 200:
                err += 1
                continue
            res = _save(
                catalog,
                raw_dir,
                purge,
                url=it["url"],
                title=it["titulo"] or page_title(art.text, it["url"]),
                orgao=veiculo_de(it["url"]),
                publish_date=it["data"] or meta_publish_date(art.text),
                content=art.text,
                tipo="noticia",
                extra={"veiculo": veiculo_de(it["url"])},
                force=force,
            )
            saved += res in ("saved", "updated")
            skip += res == "skip"
            if limit and saved >= limit:
                print(f"[sbpc/noticias] FIM (limit): {saved} salvas, {skip} puladas, {err} erros")
                return
        if pagina >= 20:  # trava de segurança na paginação
            break
    print(f"[sbpc/noticias] FIM: {saved} salvas, {skip} puladas, {err} erros")


def crawl_pdfs(http_i, catalog, raw_dir, purge, force) -> None:
    saved = skip = upd = err = 0
    for url, title, tipo in PDFS:
        r = http_i.get(url)
        if r is None or r.status_code != 200:
            print(f"[sbpc/pdfs] erro em {url}")
            err += 1
            continue
        res = _save(
            catalog,
            raw_dir,
            purge,
            url=url,
            title=title,
            orgao="78ª RA — Documentos oficiais",
            content=r.content,
            tipo=tipo,
            suffix=".pdf",
            content_type="application/pdf",
            force=force,
        )
        saved += res == "saved"
        skip += res == "skip"
        upd += res == "updated"
    print(f"[sbpc/pdfs] FIM: {saved} novos, {upd} atualizados, {skip} inalterados, {err} erros")


COLETORES = ("programacao", "minicursos", "pages78", "uffsbpc", "portal", "noticias", "pdfs")


def main() -> None:
    ap = argparse.ArgumentParser(description="Crawler da 78ª RA da SBPC + SBPC institucional")
    ap.add_argument("--limit", type=int, default=None, help="máx. de itens por coletor (teste)")
    ap.add_argument("--only", choices=COLETORES, help="roda só um coletor")
    ap.add_argument("--force", action="store_true", help="re-baixa mesmo o que já existe")
    ap.add_argument("--pause", type=float, default=0.4, help="pausa entre requisições (s)")
    args = ap.parse_args()

    settings = Settings()
    catalog = Catalog(sqlite_path(settings.catalog_dsn))
    raw_dir = Path(settings.data_dir) / "raw" / Source.SBPC.value
    raw_dir.mkdir(parents=True, exist_ok=True)
    purge = make_purge(settings.qdrant_url, settings.qdrant_collection)

    headers = {"User-Agent": UA}
    with (
        httpx.Client(timeout=30, headers=headers, follow_redirects=True) as seguro,
        # verify=False APENAS para os hosts sbpcnet com cadeia TLS incompleta (ver topo).
        httpx.Client(timeout=30, headers=headers, follow_redirects=True, verify=False) as inseg,
    ):
        http_s = Http(seguro, args.pause)
        http_i = Http(inseg, args.pause)
        if args.only in (None, "programacao"):
            crawl_programacao(http_i, catalog, raw_dir, purge, args.limit, args.force)
        if args.only in (None, "minicursos"):
            crawl_minicursos(http_i, catalog, raw_dir, purge, args.limit, args.force)
        if args.only in (None, "pages78"):
            crawl_wp_site(
                http_i,
                catalog,
                raw_dir,
                purge,
                site=BASE_RA,
                orgao="78ª RA — Site oficial",
                limit=args.limit,
                force=args.force,
            )
        if args.only in (None, "uffsbpc"):
            crawl_wp_site(
                http_s,
                catalog,
                raw_dir,
                purge,
                site=BASE_UFF_SBPC,
                orgao="78ª RA — UFF (sbpc.uff.br)",
                limit=args.limit,
                force=args.force,
                tipos=("pages", "posts"),
            )
        if args.only in (None, "portal"):
            crawl_portal(http_s, catalog, raw_dir, purge, args.force)
        if args.only in (None, "noticias"):
            crawl_noticias(http_s, http_i, catalog, raw_dir, purge, args.limit, args.force)
        if args.only in (None, "pdfs"):
            crawl_pdfs(http_i, catalog, raw_dir, purge, args.force)
    catalog.close()


if __name__ == "__main__":
    main()
