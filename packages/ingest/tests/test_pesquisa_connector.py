from pathlib import Path

import pytest
from uff_core.schemas import DocStatus, Source
from uff_ingest.connectors.pesquisa import PesquisaConnector

FIXTURES = Path(__file__).parent / "fixtures"
PAGE1 = "https://pesquisa.uff.br/?q=node&page=1"


@pytest.fixture
def html():
    return (FIXTURES / "pesquisa_page1.html").read_text(encoding="utf-8")


def test_index_urls_paginate_from_zero():
    conn = PesquisaConnector(max_pages=3)
    assert conn.index_urls() == [
        "https://pesquisa.uff.br/?q=node&page=0",
        "https://pesquisa.uff.br/?q=node&page=1",
        "https://pesquisa.uff.br/?q=node&page=2",
    ]


def test_parse_extracts_articles_with_absolute_urls(html):
    docs = PesquisaConnector().parse_index(PAGE1, html)
    assert len(docs) == 10  # títulos h2, sem duplicar os "Leia mais"
    for d in docs:
        assert d.source is Source.PESQUISA
        assert d.status is DocStatus.DISCOVERED
        assert d.url.startswith("https://pesquisa.uff.br/?q=content/")
        assert d.content_type == "text/html"
        assert d.title  # título não vazio


def test_parse_dedupes_by_url(html):
    docs = PesquisaConnector().parse_index(PAGE1, html)
    assert len({d.url for d in docs}) == len(docs)


def test_parse_real_title_present(html):
    titles = [d.title for d in PesquisaConnector().parse_index(PAGE1, html)]
    assert any("RESULTADO PRELIMINAR" in t for t in titles)
