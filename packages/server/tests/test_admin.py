import base64
import hashlib

from uff_server.admin import criar_chave, verify_basic

_HASH = hashlib.sha256(b"s3nha").hexdigest()


def _basic(user: str, pw: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()


def test_verify_basic_accepts_correct():
    assert verify_basic(_basic("admin", "s3nha"), "admin", _HASH) is True


def test_verify_basic_rejects_wrong_password():
    assert verify_basic(_basic("admin", "errada"), "admin", _HASH) is False


def test_verify_basic_rejects_wrong_user():
    assert verify_basic(_basic("root", "s3nha"), "admin", _HASH) is False


def test_verify_basic_rejects_malformed():
    assert verify_basic("", "admin", _HASH) is False
    assert verify_basic("Bearer xyz", "admin", _HASH) is False
    assert verify_basic("Basic !!!naob64", "admin", _HASH) is False


def test_criar_chave_gera_token_e_instrucoes(tmp_path):
    toks = tmp_path / "mcp_tokens.txt"
    r = criar_chave(str(toks), "hermes")
    assert r["ok"] is True
    assert r["nome"] == "hermes"
    assert len(r["token"]) == 64  # secrets.token_hex(32)
    assert r["token"] in r["instrucoes"]
    # gravado no formato "nome  token" (compatível com auth.load_token_agents / nova-chave.sh)
    line = toks.read_text().strip()
    assert line.split() == ["hermes", r["token"]]


def test_criar_chave_rejeita_duplicado(tmp_path):
    toks = tmp_path / "mcp_tokens.txt"
    assert criar_chave(str(toks), "bxat")["ok"] is True
    dup = criar_chave(str(toks), "bxat")
    assert dup["ok"] is False and "já existe" in dup["erro"]
    assert len(toks.read_text().strip().splitlines()) == 1  # não duplicou


def test_criar_chave_rejeita_nome_invalido(tmp_path):
    toks = tmp_path / "mcp_tokens.txt"
    for ruim in ("", "com espaço", "a", "x" * 33, "in/val"):
        assert criar_chave(str(toks), ruim)["ok"] is False
    assert not toks.exists()  # nada gravado


def test_criar_chave_sem_arquivo_configurado():
    r = criar_chave(None, "hermes")
    assert r["ok"] is False
