import datetime as dt

import pytest
from uff_core.catalog import Catalog
from uff_core.schemas import DocStatus, Document, Source


@pytest.fixture
def catalog():
    cat = Catalog(":memory:")
    yield cat
    cat.close()


def _doc(url="https://boletimdeservico.uff.br/2024/09/113-24.pdf", **kw):
    return Document(source=Source.BOLETIM, url=url, **kw)


def test_upsert_assigns_id_and_is_retrievable(catalog):
    saved = catalog.upsert(_doc(title="Boletim 113"))
    assert saved.id is not None
    fetched = catalog.get(saved.id)
    assert fetched is not None
    assert fetched.url == saved.url
    assert fetched.title == "Boletim 113"
    assert fetched.status is DocStatus.DISCOVERED


def test_upsert_same_url_dedups_and_updates(catalog):
    first = catalog.upsert(_doc(title="antigo"))
    second = catalog.upsert(_doc(title="novo", orgao="PROGEPE"))
    assert first.id == second.id
    assert catalog.count() == 1
    fetched = catalog.get(first.id)
    assert fetched.title == "novo"
    assert fetched.orgao == "PROGEPE"


def test_upsert_preserves_status_on_rediscovery(catalog):
    saved = catalog.upsert(_doc())
    catalog.set_status(saved.id, DocStatus.INDEXED)
    # Redescobrir no índice não deve rebaixar o status já avançado.
    again = catalog.upsert(_doc(title="mudou o título"))
    assert catalog.get(again.id).status is DocStatus.INDEXED


def test_same_url_different_source_are_distinct(catalog):
    catalog.upsert(Document(source=Source.BOLETIM, url="https://x/1"))
    catalog.upsert(Document(source=Source.RESOLUCAO, url="https://x/1"))
    assert catalog.count() == 2


def test_list_by_status_filters(catalog):
    a = catalog.upsert(_doc(url="https://x/a"))
    catalog.upsert(_doc(url="https://x/b"))
    catalog.set_status(a.id, DocStatus.FETCHED)
    fetched = catalog.list_by_status(DocStatus.FETCHED)
    discovered = catalog.list_by_status(DocStatus.DISCOVERED)
    assert [d.id for d in fetched] == [a.id]
    assert len(discovered) == 1


def test_roundtrip_preserves_date_and_extra(catalog):
    saved = catalog.upsert(
        _doc(publish_date=dt.date(2024, 9, 9), extra={"ano_boletim": 2024, "secao": "II"})
    )
    fetched = catalog.get(saved.id)
    assert fetched.publish_date == dt.date(2024, 9, 9)
    assert fetched.extra == {"ano_boletim": 2024, "secao": "II"}


def test_get_by_url_returns_none_when_absent(catalog):
    assert catalog.get_by_url(Source.BOLETIM, "https://nao/existe") is None
    saved = catalog.upsert(_doc(url="https://x/exists"))
    found = catalog.get_by_url(Source.BOLETIM, "https://x/exists")
    assert found is not None and found.id == saved.id
