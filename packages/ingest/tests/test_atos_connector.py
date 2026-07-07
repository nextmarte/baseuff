import datetime as dt
from pathlib import Path

import pytest
from uff_core.schemas import DocStatus, Source
from uff_ingest.connectors.atos import AtosNormativosConnector

FIXTURES = Path(__file__).parent / "fixtures"
LISTAGEM = "https://atosnormativos.uff.br/listagem-dos-atos/"


@pytest.fixture
def html():
    return (FIXTURES / "atos_listagem.html").read_text(encoding="utf-8")


def test_index_urls():
    assert AtosNormativosConnector().index_urls() == [LISTAGEM]


def test_parse_extracts_atos_and_ignores_non_atos(html):
    docs = AtosNormativosConnector().parse_index(LISTAGEM, html)
    # 2 portarias + 1 resolução; ignora "GT Revisa UFF" e a lei externa (.htm)
    assert len(docs) == 3
    for d in docs:
        assert d.source is Source.RESOLUCAO
        assert d.status is DocStatus.DISCOVERED
        assert d.url.split("#")[0].lower().endswith(".pdf")


def test_parse_metadata_of_portaria(html):
    docs = {d.numero: d for d in AtosNormativosConnector().parse_index(LISTAGEM, html)}
    p = docs["67.542"]
    assert p.extra["tipo"] == "portaria"
    assert p.publish_date == dt.date(2020, 9, 30)
    assert p.extra["page"] == 46
    assert p.url.endswith("180-20.pdf#page=46")
    assert "67.542" in p.title


def test_parse_keeps_page_anchor_and_encoded_url(html):
    docs = {d.numero: d for d in AtosNormativosConnector().parse_index(LISTAGEM, html)}
    p = docs["68.236"]
    assert p.publish_date == dt.date(2021, 6, 2)
    assert p.extra["page"] == 101
    assert "RETIFICADO" in p.url and p.url.endswith("#page=101")


def test_parse_resolucao_type(html):
    docs = {d.numero: d for d in AtosNormativosConnector().parse_index(LISTAGEM, html)}
    r = docs["155"]
    assert r.extra["tipo"] == "resolucao"
    assert r.publish_date == dt.date(2019, 3, 13)
