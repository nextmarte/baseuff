"""Adaptador de parsing com Docling (roda no skynet01, usa GPU/OCR).

Converte um PDF (inclusive escaneado, via OCR) em Markdown com layout/tabelas
preservados. O ``DocumentConverter`` é caro de instanciar (carrega modelos), então
é reutilizado entre chamadas.
"""

from __future__ import annotations

from pathlib import Path

_converter = None


def _get_converter():
    global _converter
    if _converter is None:
        from docling.document_converter import DocumentConverter

        _converter = DocumentConverter()
    return _converter


def parse_pdf(path: str | Path) -> str:
    """Converte um PDF em Markdown."""
    result = _get_converter().convert(str(path))
    return result.document.export_to_markdown()
