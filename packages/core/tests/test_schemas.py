import datetime as dt

import pytest
from pydantic import ValidationError
from uff_core.schemas import DocStatus, Document, Source


def test_document_minimal_defaults_to_discovered():
    doc = Document(source=Source.BOLETIM, url="https://boletimdeservico.uff.br/x/113-24.pdf")
    assert doc.id is None
    assert doc.status is DocStatus.DISCOVERED
    assert doc.extra == {}


def test_document_accepts_full_metadata():
    doc = Document(
        source=Source.BOLETIM,
        url="https://boletimdeservico.uff.br/x/113-24.pdf",
        title="Boletim de Serviço nº 113",
        numero="113",
        publish_date=dt.date(2024, 9, 9),
        orgao="PROGEPE",
        content_type="application/pdf",
        checksum="abc123",
        extra={"ano_boletim": 2024},
    )
    assert doc.numero == "113"
    assert doc.publish_date == dt.date(2024, 9, 9)
    assert doc.extra["ano_boletim"] == 2024


def test_source_and_status_are_string_enums():
    # Serializáveis como string simples (para persistir no catálogo/payload).
    assert Source.BOLETIM.value == "boletim"
    assert DocStatus.PENDING_EMBED.value == "pending_embed"


def test_document_requires_source_and_url():
    with pytest.raises(ValidationError):
        Document(url="https://x")  # falta source
    with pytest.raises(ValidationError):
        Document(source=Source.BOLETIM)  # falta url
