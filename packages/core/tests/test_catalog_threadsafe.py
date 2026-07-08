from concurrent.futures import ThreadPoolExecutor

from uff_core.catalog import Catalog
from uff_core.schemas import Document, Source


def test_stats_is_thread_safe(tmp_path):
    """info()/doc pública chamam stats() numa thread de worker do MCP; a conexão
    SQLite da thread principal não pode ser usada em outra thread (bug relatado)."""
    cat = Catalog(str(tmp_path / "c.db"))
    cat.upsert(Document(source=Source.BOLETIM, url="u1", numero="1", publish_date=None))

    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(cat.stats).result()  # roda em OUTRA thread — não pode explodir

    assert "boletim" in result
    assert result["boletim"]["documentos"] == 1
