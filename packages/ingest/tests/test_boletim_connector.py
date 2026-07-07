import datetime as dt
from pathlib import Path

import pytest
from uff_core.schemas import DocStatus, Source
from uff_ingest.connectors.boletim import BoletimConnector

FIXTURES = Path(__file__).parent / "fixtures"
BS_2024 = "https://boletimdeservico.uff.br/boletins/bs-2024/"


@pytest.fixture
def year_html():
    return (FIXTURES / "boletim_bs-2024.html").read_text(encoding="utf-8")


def test_index_urls_span_start_year_to_current():
    conn = BoletimConnector(start_year=2010, current_year=2024)
    urls = conn.index_urls()
    assert urls[0] == "https://boletimdeservico.uff.br/boletins/bs-2010"
    assert urls[-1] == "https://boletimdeservico.uff.br/boletins/bs-2024"
    assert len(urls) == 15  # 2010..2024 inclusive


def test_index_urls_single_year():
    conn = BoletimConnector(start_year=2010, current_year=2010)
    assert conn.index_urls() == ["https://boletimdeservico.uff.br/boletins/bs-2010"]


def test_parse_index_extracts_only_valid_editions(year_html):
    conn = BoletimConnector(start_year=2010, current_year=2024)
    docs = conn.parse_index(BS_2024, year_html)
    # ignora png, portal e a referência cruzada de 2023 (texto sem "N, de DD/MM/AAAA")
    assert sorted(d.numero for d in docs) == ["148", "156", "159"]
    for d in docs:
        assert d.source is Source.BOLETIM
        assert d.status is DocStatus.DISCOVERED
        assert d.url.startswith("https://boletimdeservico.uff.br/")
        assert d.url.lower().endswith(".pdf")


def test_parse_index_parses_date_and_title(year_html):
    docs = {
        d.numero: d
        for d in BoletimConnector(start_year=2010, current_year=2024).parse_index(
            BS_2024, year_html
        )
    }
    d159 = docs["159"]
    assert d159.publish_date == dt.date(2024, 12, 27)
    assert "159" in d159.title and "27/12/2024" in d159.title
    assert d159.extra.get("ano") == 2024


def test_parse_index_resolves_relative_url_and_flags_retificado(year_html):
    docs = {
        d.numero: d
        for d in BoletimConnector(start_year=2010, current_year=2024).parse_index(
            BS_2024, year_html
        )
    }
    d156 = docs["156"]
    assert d156.url == (
        "https://boletimdeservico.uff.br/wp-content/uploads/sites/620/2024/12/156-24-RETIFICADO.pdf"
    )
    assert d156.extra.get("retificado") is True
    assert d156.publish_date == dt.date(2024, 12, 18)
