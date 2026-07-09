from concurrent.futures import ThreadPoolExecutor

from uff_core.querylog import QueryLog


def _entry(**over):
    e = {
        "agent": "bxat",
        "tool": "search",
        "query": "licença capacitação",
        "source": "boletim",
        "date_from": None,
        "date_to": None,
        "n_results": 3,
        "latency_ms": 640,
        "top_results": [{"doc_id": 12, "score": 0.9}],
        "error": None,
    }
    e.update(over)
    return e


def test_log_persists_and_reads_back(tmp_path):
    ql = QueryLog(str(tmp_path / "q.db"))
    ql.log(_entry())
    rows = ql.recent(limit=10)
    assert len(rows) == 1
    r = rows[0]
    assert r["agent"] == "bxat"
    assert r["tool"] == "search"
    assert r["query"] == "licença capacitação"
    assert r["n_results"] == 3
    assert r["latency_ms"] == 640
    assert r["top_results"] == [{"doc_id": 12, "score": 0.9}]  # json ida-e-volta
    assert r["ts"]  # timestamp preenchido pelo default


def test_log_is_thread_safe(tmp_path):
    """As tools do MCP logam em worker threads; a conexão SQLite não pode cruzar threads."""
    ql = QueryLog(str(tmp_path / "q.db"))
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda i: ql.log(_entry(query=f"q{i}")), range(20)))
    assert len(ql.recent(limit=100)) == 20


def test_zero_results_query_is_recorded(tmp_path):
    ql = QueryLog(str(tmp_path / "q.db"))
    ql.log(_entry(query="kubernetes", n_results=0, top_results=[]))
    assert ql.recent()[0]["n_results"] == 0


def test_get_returns_raw_query_by_id(tmp_path):
    ql = QueryLog(str(tmp_path / "q.db"))
    ql.log(_entry(query="Fulano CPF 123.456.789-00", source="boletim"))
    qid = ql.recent()[0]["id"]
    got = ql.get(qid)
    assert got is not None
    assert got["query"] == "Fulano CPF 123.456.789-00"  # crua (o painel mascara na saída)
    assert got["source"] == "boletim"
    assert ql.get(999999) is None


def test_detail_buckets(tmp_path):
    ql = QueryLog(str(tmp_path / "q.db"))
    ql.log(_entry(tool="dossie", query="Ninguém", n_results=0))  # lacuna
    ql.log(_entry(tool="search", query="ok", n_results=5, latency_ms=100))
    ql.log(_entry(tool="search", query="lenta", n_results=1, latency_ms=9000))
    ql.log(_entry(tool="search", query="quebrou", error="boom"))  # erro
    lac = ql.detail("lacunas")
    assert [r["query"] for r in lac] == ["Ninguém"]
    assert all("id" in r for r in lac)
    err = ql.detail("erros")
    assert [r["query"] for r in err] == ["quebrou"]
    lentas = ql.detail("lentas")
    assert lentas[0]["query"] == "lenta"  # ordenado por latência desc
    assert ql.detail("inexistente") == []
