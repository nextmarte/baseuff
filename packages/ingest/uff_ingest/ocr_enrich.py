"""Enriquecimento de HTML com OCR de imagens (telas de sistema nos tutoriais).

Tutoriais do STI têm screenshots com texto útil (menus, botões, campos) que o
extrator de texto normal descartaria. Esta função baixa cada ``<img>``, roda OCR
e costura o texto no lugar da imagem como ``[tela: …]``, tornando o conteúdo
visual pesquisável. ``fetch_image`` e ``ocr_image`` são injetados (testável e
independente do backend de OCR/rede).
"""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urljoin

from selectolax.parser import HTMLParser


def enrich_html_with_ocr(
    html: str,
    *,
    base_url: str,
    fetch_image: Callable[[str], bytes | None],
    ocr_image: Callable[[bytes], str],
    marker: str = "tela",
) -> str:
    """Substitui cada imagem pelo texto extraído dela via OCR (``[marker: texto]``).

    Imagens indisponíveis (fetch retorna ``None``) ou sem texto (OCR vazio) são
    apenas removidas do fluxo, preservando o texto original ao redor.
    """
    tree = HTMLParser(html)
    for img in tree.css("img"):
        src = (img.attributes.get("src") or "").strip()
        if not src:
            continue
        data = fetch_image(urljoin(base_url, src))
        text = ocr_image(data).strip() if data else ""
        if text:
            img.replace_with(f"<p>[{marker}: {text}]</p>")
        else:
            img.decompose()
    return tree.html or ""
