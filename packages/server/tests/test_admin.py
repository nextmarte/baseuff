import base64
import hashlib

from uff_server.admin import verify_basic

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
