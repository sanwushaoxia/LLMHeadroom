"""CLI 测试。"""

from __future__ import annotations

import pytest

from headroom.cli import main


@pytest.fixture()
def big_log_file(tmp_path):
    p = tmp_path / "app.log"
    p.write_text(
        "\n".join(f"2024-01-01 00:{i // 60:02d}:{i % 60:02d} DEBUG healthcheck ok {i}" for i in range(300)),
        encoding="utf-8",
    )
    return p


def test_compress_file(tmp_path, big_log_file, capsys):
    rc = main(["compress", str(big_log_file)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "[headroom:ccr://" in out


def test_compress_stdin(tmp_path, capsys, monkeypatch):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("plain short text"))
    rc = main(["compress", "-"])
    assert rc == 0
    assert "plain short text" in capsys.readouterr().out


def test_retrieve_roundtrip(tmp_path, big_log_file, capsys):
    main(["compress", str(big_log_file)])
    out = capsys.readouterr().out
    cid = out.splitlines()[0].split("ccr://")[1][:8]
    rc = main(["retrieve", cid])
    assert rc == 0
    assert "healthcheck ok 299" in capsys.readouterr().out


def test_retrieve_missing(tmp_path, capsys):
    rc = main(["retrieve", "ffffffff"])
    assert rc == 1


def test_stats(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("HEADROOM_DB", str(tmp_path / "s.db"))
    rc = main(["stats"])
    assert rc == 0
    assert "db_path" in capsys.readouterr().out
