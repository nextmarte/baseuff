import datetime as dt
import re

from uff_core.chunking import build_context_prefix, chunk_document, chunk_text
from uff_core.schemas import Document, Source


def test_short_text_is_single_chunk():
    chunks = chunk_text("Um parágrafo curto.", target_chars=1000)
    assert chunks == ["Um parágrafo curto."]


def test_empty_text_yields_no_chunks():
    assert chunk_text("   \n\n  ") == []


def test_long_text_splits_with_bounded_size():
    text = "Frase de teste número. " * 300  # ~6900 chars, um parágrafo só
    chunks = chunk_text(text, target_chars=1000, overlap_chars=100)
    assert len(chunks) >= 5
    assert all(len(c) <= 1200 for c in chunks)  # target + tolerância


def test_respects_paragraph_boundaries():
    text = "Parágrafo um.\n\nParágrafo dois.\n\nParágrafo três."
    chunks = chunk_text(text, target_chars=20, overlap_chars=0)
    assert chunks == ["Parágrafo um.", "Parágrafo dois.", "Parágrafo três."]


def test_overlap_carries_tail_between_chunks():
    text = " ".join(f"Sentenca numero {i}." for i in range(1, 61))
    chunks = chunk_text(text, target_chars=200, overlap_chars=60)
    assert len(chunks) >= 3
    last_num = re.findall(r"Sentenca numero (\d+)", chunks[0])[-1]
    assert f"Sentenca numero {last_num}" in chunks[1]  # sobreposição


def test_build_context_prefix_for_boletim():
    doc = Document(source=Source.BOLETIM, url="x", numero="159", publish_date=dt.date(2024, 12, 27))
    prefix = build_context_prefix(doc)
    assert prefix.startswith("[") and prefix.endswith("]")
    assert "Boletim" in prefix and "159" in prefix and "27/12/2024" in prefix


def test_chunk_document_sets_prefix_indices_and_docid():
    doc = Document(
        source=Source.BOLETIM, url="x", numero="159", publish_date=dt.date(2024, 12, 27), id=7
    )
    text = "Parágrafo. " * 400  # força vários chunks
    chunks = chunk_document(doc, text, target_chars=1000, overlap_chars=100)
    assert len(chunks) >= 2
    assert [c.index for c in chunks] == list(range(len(chunks)))
    assert all(c.doc_id == 7 for c in chunks)
    assert all("159" in (c.context_prefix or "") for c in chunks)
    assert chunks[0].embedding_text.startswith("[")
    assert chunks[0].text in chunks[0].embedding_text
