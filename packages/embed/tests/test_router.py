import fitz  # PyMuPDF
import pytest
from uff_embed.router import extract_text, needs_ocr, parse_smart


@pytest.fixture
def text_pdf(tmp_path):
    """PDF nascido digital: todas as páginas com texto denso."""
    path = tmp_path / "digital.pdf"
    doc = fitz.open()
    for i in range(3):
        page = doc.new_page()
        lines = [f"BOLETIM DE SERVIÇO página {i}."] + ["Conteúdo textual denso."] * 30
        page.insert_text((72, 72), "\n".join(lines), fontsize=11)
    doc.save(path)
    return path


@pytest.fixture
def scanned_pdf(tmp_path):
    """Simula escaneado: páginas sem camada de texto."""
    path = tmp_path / "scanned.pdf"
    doc = fitz.open()
    for _ in range(3):
        doc.new_page()  # página vazia = sem texto extraível
    doc.save(path)
    return path


def test_extract_text_gets_content(text_pdf):
    text = extract_text(text_pdf)
    assert "BOLETIM DE SERVIÇO página 0" in text
    assert len(text) > 1000


def test_needs_ocr_false_for_digital(text_pdf):
    assert needs_ocr(text_pdf) is False


def test_needs_ocr_true_for_scanned(scanned_pdf):
    assert needs_ocr(scanned_pdf) is True


def test_parse_smart_uses_fast_path_for_digital(text_pdf):
    # não deve tocar no Docling (nem importá-lo) para PDFs digitais
    text = parse_smart(text_pdf, ocr_fallback=None)
    assert "BOLETIM DE SERVIÇO" in text


def test_parse_smart_routes_scanned_to_fallback(scanned_pdf):
    called = {}

    def fake_ocr(path):
        called["path"] = path
        return "texto vindo do OCR"

    text = parse_smart(scanned_pdf, ocr_fallback=fake_ocr)
    assert text == "texto vindo do OCR"
    assert called["path"] == scanned_pdf
