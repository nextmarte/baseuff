import pytest
from uff_core.config import Settings, sqlite_path

_UFF_ENV = [
    "UFF_QDRANT_URL",
    "UFF_QDRANT_COLLECTION",
    "UFF_CATALOG_DSN",
    "UFF_DATA_DIR",
    "UFF_USER_AGENT",
    "UFF_REQUESTS_PER_SECOND",
    "UFF_MAX_CONCURRENCY",
    "UFF_BOLETIM_START_YEAR",
]


@pytest.fixture
def clean_env(monkeypatch):
    for key in _UFF_ENV:
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def test_defaults(clean_env):
    s = Settings(_env_file=None)
    assert s.qdrant_url == "http://localhost:6333"
    assert s.qdrant_collection == "uff_chunks"
    assert s.boletim_start_year == 2010
    assert s.max_concurrency == 4
    assert s.requests_per_second == pytest.approx(1.0)


def test_env_override(clean_env):
    clean_env.setenv("UFF_BOLETIM_START_YEAR", "2015")
    clean_env.setenv("UFF_QDRANT_URL", "http://qdrant:6333")
    clean_env.setenv("UFF_MAX_CONCURRENCY", "8")
    s = Settings(_env_file=None)
    assert s.boletim_start_year == 2015
    assert s.qdrant_url == "http://qdrant:6333"
    assert s.max_concurrency == 8


def test_sqlite_path_from_dsn():
    assert sqlite_path("sqlite:///data/catalog.db") == "data/catalog.db"
    assert sqlite_path("sqlite:////abs/catalog.db") == "/abs/catalog.db"


def test_sqlite_path_rejects_non_sqlite():
    with pytest.raises(ValueError):
        sqlite_path("postgresql://localhost/uff")
