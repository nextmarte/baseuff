from uff_server.auth import extract_bearer, is_authorized, load_tokens


def test_extract_bearer_variants():
    assert extract_bearer("Bearer abc123") == "abc123"
    assert extract_bearer("bearer abc123") == "abc123"  # esquema case-insensitive
    assert extract_bearer("Bearer   espacado  ") == "espacado"
    assert extract_bearer("Basic xyz") == ""
    assert extract_bearer("") == ""
    assert extract_bearer("Bearer") == ""


def test_load_tokens_parses_agent_token_lines(tmp_path):
    f = tmp_path / "toks.txt"
    f.write_text("# comentário\nhermes   AAA\nopenclaw  BBB\n\nsolo\n", encoding="utf-8")
    assert load_tokens(str(f)) == {"AAA", "BBB", "solo"}


def test_load_tokens_missing_file_is_empty(tmp_path):
    assert load_tokens(str(tmp_path / "nao_existe.txt")) == set()


def test_is_authorized():
    tokens = {"AAA", "BBB"}
    assert is_authorized("Bearer AAA", tokens) is True
    assert is_authorized("Bearer BBB", tokens) is True
    assert is_authorized("Bearer WRONG", tokens) is False
    assert is_authorized("", tokens) is False
    assert is_authorized("Bearer ", tokens) is False
    assert is_authorized("Bearer AAA", set()) is False  # sem chaves = ninguém entra
