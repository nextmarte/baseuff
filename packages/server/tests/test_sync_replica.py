"""Testes offline do sync da réplica Modal (scripts/sync_replica.py): manifest,
comando do volume, parse do snapshot e a saída graciosa sem a CLI instalada."""

import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))

import sync_replica as s  # noqa: E402


def test_montar_manifest_tamanhos_e_ausentes(tmp_path):
    snap = tmp_path / "uff_chunks.snapshot"
    snap.write_bytes(b"x" * 123)
    ausente = tmp_path / "nao_existe.txt"
    quando = dt.datetime(2026, 7, 21, 6, 0, 0)
    m = s.montar_manifest(511140, {"snapshot": snap, "tokens": ausente}, quando)
    assert m["quando"] == "2026-07-21T06:00:00"
    assert m["collection"] == "uff_chunks"
    assert m["points"] == 511140
    assert m["arquivos"] == {"snapshot": 123}  # arquivo ausente fica fora
    json.dumps(m)  # serializável


def test_volume_put_monta_comando_da_cli(tmp_path):
    chamadas = []

    def executar(cmd, check):
        chamadas.append((cmd, check))

    s.volume_put(tmp_path / "catalog.db", "/catalog.db", executar=executar)
    (cmd, check) = chamadas[0]
    assert check is True
    assert cmd[0] == s.MODAL
    assert cmd[1:4] == ["volume", "put", "baseuff-data"]
    assert cmd[4].endswith("catalog.db")
    assert cmd[5] == "/catalog.db"
    assert "--force" in cmd


def test_criar_snapshot_devolve_nome(monkeypatch):
    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"result": {"name": "uff_chunks-2026-07-21.snapshot"}}

    class HttpxStub:
        @staticmethod
        def post(url, params=None, timeout=None):
            assert url.endswith("/collections/uff_chunks/snapshots")
            assert params == {"wait": "true"}
            return Resp()

    monkeypatch.setattr(s, "httpx", HttpxStub)
    assert s.criar_snapshot("http://localhost:6333") == "uff_chunks-2026-07-21.snapshot"


def test_main_sem_cli_da_modal_sai_com_zero(monkeypatch, capsys):
    """Sem a CLI instalada o sync é um no-op: cron do ultron segue funcionando."""
    monkeypatch.setattr(s, "MODAL", "/caminho/inexistente/modal")
    assert s.main() == 0
    assert "pulando sync" in capsys.readouterr().out
