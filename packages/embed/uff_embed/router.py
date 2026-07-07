"""Roteador de parsing híbrido: PyMuPDF (rápido) com fallback Docling/OCR.

Medição real no acervo: PyMuPDF extrai um boletim em ~0,04s contra 7–38s do
Docling — ~400× mais rápido. Quase todos os boletins (mesmo os de 2010) têm
camada de texto nativa; o Docling+OCR fica reservado aos raros PDFs de fato
escaneados, detectados pela densidade de texto por página.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import fitz  # PyMuPDF

# Página com menos que isso de texto é considerada "quase vazia" (capa/escaneada).
MIN_CHARS_PER_PAGE = 200
# Fração de páginas quase vazias acima da qual o doc vai para OCR.
SPARSE_RATIO = 0.2


def extract_text(path: str | Path) -> str:
    """Extração rápida de texto (camada nativa) com PyMuPDF."""
    with fitz.open(str(path)) as doc:
        return "\n\n".join(page.get_text() for page in doc)


def needs_ocr(
    path: str | Path,
    *,
    min_chars_per_page: int = MIN_CHARS_PER_PAGE,
    sparse_ratio: float = SPARSE_RATIO,
) -> bool:
    """True se o PDF parece escaneado (texto nativo insuficiente)."""
    with fitz.open(str(path)) as doc:
        if len(doc) == 0:
            return True
        sparse = sum(1 for page in doc if len(page.get_text()) < min_chars_per_page)
        return (sparse / len(doc)) > sparse_ratio


def _docling_ocr(path: str | Path) -> str:
    from .parse import parse_pdf  # import tardio: docling é pesado

    return parse_pdf(path)


def parse_smart(
    path: str | Path,
    *,
    ocr_fallback: Callable[[str | Path], str] | None = _docling_ocr,
) -> str:
    """PyMuPDF para PDFs com texto nativo; ``ocr_fallback`` (Docling) para escaneados.

    ``ocr_fallback=None`` desliga o fallback (útil em testes/ambientes sem docling).
    """
    if ocr_fallback is not None and needs_ocr(path):
        return ocr_fallback(path)
    return extract_text(path)
