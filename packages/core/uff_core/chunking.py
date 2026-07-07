"""Chunking estrutural + prefixo contextual (Contextual Retrieval).

``chunk_text`` quebra o texto respeitando limites de parágrafo/sentença, com
tamanho-alvo e sobreposição. ``build_context_prefix`` monta um prefixo denso de
metadados (fonte, número, data, órgão) e ``chunk_document`` combina os dois em
:class:`~uff_core.schemas.Chunk` prontos para vetorização.
"""

from __future__ import annotations

import re

from uff_core.schemas import Chunk, Document, Source

_SOURCE_LABEL = {
    Source.BOLETIM: "Boletim de Serviço",
    Source.RESOLUCAO: "Ato Normativo",
    Source.STI_MANUAL: "Manual STI",
    Source.STI_KB: "Base de Conhecimento STI",
    Source.PESQUISA: "Portal da Pesquisa",
}

_PARAGRAPH_RE = re.compile(r"\n\s*\n")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def _hard_split(segment: str, max_chars: int) -> list[str]:
    return [segment[i : i + max_chars] for i in range(0, len(segment), max_chars)]


def _segments(text: str, max_chars: int) -> list[str]:
    """Quebra o texto em segmentos atômicos <= max_chars (parágrafo → sentença → janela)."""
    segments: list[str] = []
    for paragraph in _PARAGRAPH_RE.split(text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) <= max_chars:
            segments.append(paragraph)
            continue
        for sentence in _SENTENCE_RE.split(paragraph):
            sentence = sentence.strip()
            if not sentence:
                continue
            if len(sentence) <= max_chars:
                segments.append(sentence)
            else:
                segments.extend(_hard_split(sentence, max_chars))
    return segments


def chunk_text(
    text: str,
    *,
    target_chars: int = 1200,
    overlap_chars: int = 150,
    min_chars: int = 1,
) -> list[str]:
    """Quebra ``text`` em trechos ~``target_chars`` com sobreposição de ``overlap_chars``."""
    segments = _segments(text, target_chars)
    if not segments:
        return []

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for seg in segments:
        extra = len(seg) + (1 if current else 0)
        if current and current_len + extra > target_chars:
            chunks.append(" ".join(current))
            current, current_len = _overlap_tail(current, overlap_chars)
        current.append(seg)
        current_len += len(seg) + (1 if current[:-1] else 0)

    if current:
        chunks.append(" ".join(current))
    return [c for c in chunks if len(c) >= min_chars]


def _overlap_tail(segments: list[str], overlap_chars: int) -> tuple[list[str], int]:
    """Segmentos finais que cabem em ``overlap_chars``, para iniciar o próximo chunk."""
    if overlap_chars <= 0:
        return [], 0
    tail: list[str] = []
    length = 0
    for seg in reversed(segments):
        add = len(seg) + (1 if tail else 0)
        if length + add > overlap_chars:
            break
        tail.insert(0, seg)
        length += add
    return tail, length


def build_context_prefix(doc: Document) -> str:
    """Prefixo denso de metadados, ex.: ``[Boletim de Serviço · nº 159 · 27/12/2024]``."""
    parts: list[str] = [_SOURCE_LABEL.get(doc.source, doc.source.value)]
    if doc.numero:
        parts.append(f"nº {doc.numero}")
    if doc.publish_date:
        parts.append(doc.publish_date.strftime("%d/%m/%Y"))
    if doc.orgao:
        parts.append(f"Órgão: {doc.orgao}")
    return "[" + " · ".join(parts) + "]"


def chunk_document(
    doc: Document,
    text: str,
    *,
    target_chars: int = 1200,
    overlap_chars: int = 150,
) -> list[Chunk]:
    prefix = build_context_prefix(doc)
    pieces = chunk_text(text, target_chars=target_chars, overlap_chars=overlap_chars)
    return [
        Chunk(doc_id=doc.id, index=i, text=piece, context_prefix=prefix)
        for i, piece in enumerate(pieces)
    ]
