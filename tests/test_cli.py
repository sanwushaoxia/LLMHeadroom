"""CLI 新契约测试。"""

from __future__ import annotations

import io
import json

import pytest

from headroom.cli import main


@pytest.fixture()
def big_log_file(tmp_path):
    path = tmp_path / "app.log"
    path.write_text(
        "\n".join(
            f"2024-01-01 00:{i // 60:02d}:{i % 60:02d} DEBUG healthcheck ok {i}" for i in range(300)
        ),
        encoding="utf-8",
    )
    return path


def test_convert_jsonl_and_array(tmp_path, capsys):
    path = tmp_path / "app.txt"
    path.write_text("12:00:00 INFO app.cc:7 hello\n", encoding="utf-8")

    assert main(["convert", str(path), "--from", "txt", "--to", "json"]) == 0
    jsonl = capsys.readouterr().out
    assert json.loads(jsonl)["type"] == "log"

    assert main(["convert", str(path), "--format", "array"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["source_file"] == "app.cc"


def test_convert_stdin_and_output_file(tmp_path, capsys, monkeypatch):
    source = "12:00:00 INFO stdin.cc:1 消息\n"
    monkeypatch.setattr("sys.stdin", io.StringIO(source))
    output = tmp_path / "converted.jsonl"
    assert main(["convert", "-", "--output", str(output), "--no-raw"]) == 0
    assert capsys.readouterr().out == ""
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["message"] == "消息"
    assert "raw" not in payload


def test_convert_uses_config_encoding(tmp_path, capsys, monkeypatch):
    path = tmp_path / "latin1.txt"
    path.write_bytes(b"12:00:00 INFO app.cc:1 byte=\xf2\n")
    config = write_config(tmp_path, {"encoding": "latin-1"})
    monkeypatch.setenv("HEADROOM_CONFIG", str(config))
    assert main(["convert", str(path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "byte=\u00f2" in payload["message"]


def test_convert_decode_error_has_stable_code(tmp_path, capsys):
    path = tmp_path / "invalid.txt"
    path.write_bytes(b"12:00:00 INFO app.cc:1 byte=\xf2\n")
    assert main(["convert", str(path)]) == 2
    error = json.loads(capsys.readouterr().err)
    assert error["error"]["code"] == "INPUT_DECODE_ERROR"


def test_convert_does_not_change_compression_stats(tmp_path, capsys):
    path = tmp_path / "app.txt"
    path.write_text("12:00:00 INFO app.cc:1 hello\n", encoding="utf-8")
    assert main(["convert", str(path)]) == 0
    capsys.readouterr()
    assert main(["stats", "--json"]) == 0
    stats = json.loads(capsys.readouterr().out)
    assert stats["calls"] == 0


def test_retrieve_json_page(big_log_file, capsys):
    main(["compress", str(big_log_file), "--json"])
    payload = json.loads(capsys.readouterr().out)
    cid = payload["ccr_id"]
    assert main(["retrieve", cid, "--offset", "0", "--limit", "50", "--json"]) == 0
    page = json.loads(capsys.readouterr().out)
    assert page["ok"] is True
    assert page["offset"] == 0
    assert page["has_more"] is True
    assert page["next_offset"] == 50


def test_stats_json_persists(big_log_file, capsys):
    main(["compress", str(big_log_file), "--json"])
    capsys.readouterr()
    assert main(["stats", "--json"]) == 0
    stats = json.loads(capsys.readouterr().out)
    assert stats["ok"] is True
    assert stats["calls"] == 1


def test_missing_retrieve_has_stable_exit(capsys):
    assert main(["retrieve", "ffffffff"]) == 1
    error = json.loads(capsys.readouterr().err)
    assert error["error"]["code"] == "CCR_NOT_FOUND"


def test_compress_non_utf8_with_explicit_encoding(tmp_path, capsys):
    path = tmp_path / "latin1.log"
    path.write_bytes(b"INFO ok\nERROR byte=\xf2\n")
    assert main(["compress", str(path), "--encoding", "latin-1", "--hint", "log"]) == 0
    assert "byte=" in capsys.readouterr().out


def test_compress_max_content_override(tmp_path, capsys):
    path = tmp_path / "large.log"
    path.write_text("ERROR large\n" * 100, encoding="utf-8")
    assert main(["compress", str(path), "--max-content", "4096"]) == 0
    assert "large" in capsys.readouterr().out


def test_compress_decode_error_has_stable_code(tmp_path, capsys):
    path = tmp_path / "invalid.log"
    path.write_bytes(b"ERROR byte=\xf2\n")
    assert main(["compress", str(path)]) == 2
    error = json.loads(capsys.readouterr().err)
    assert error["error"]["code"] == "INPUT_DECODE_ERROR"
    assert error["error"]["details"]["position"] == 11


# ------------------------------------------------- CLI config file


def write_config(tmp_path, payload, name="config.json"):
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_compress_uses_config_defaults(tmp_path, capsys, monkeypatch):
    log = tmp_path / "latin1.log"
    log.write_bytes(b"INFO ok\nERROR byte=\xf2\n")
    write_config(tmp_path, {"encoding": "latin-1", "max_content": 10485760, "hint": "log"})
    monkeypatch.setenv("HEADROOM_CONFIG", str(tmp_path / "config.json"))
    assert main(["compress", str(log), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["compressor"] == "log"
    assert payload["hint"] == "log"
    # latin-1 的 0xf2 字节会以 UTF-8 双字节存储，original_bytes 是解码后文本的 UTF-8 大小。
    assert payload["original_bytes"] == len(log.read_bytes()) + 1


def test_compress_cli_overrides_config(tmp_path, capsys, monkeypatch):
    log = tmp_path / "app.log"
    log.write_text("DEBUG line\n" * 200, encoding="utf-8")
    write_config(tmp_path, {"encoding": "latin-1", "max_content": 10, "hint": "text"})
    monkeypatch.setenv("HEADROOM_CONFIG", str(tmp_path / "config.json"))
    assert main(["compress", str(log), "--max-content", "4096", "--hint", "log", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["compressor"] == "log"
    assert payload["hint"] == "log"


def test_env_max_content_beats_config(tmp_path, capsys, monkeypatch):
    log = tmp_path / "app.log"
    log.write_text("ERROR x\n" * 500, encoding="utf-8")
    write_config(tmp_path, {"max_content": 10485760})
    monkeypatch.setenv("HEADROOM_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("HEADROOM_MAX_CONTENT", "4096")
    assert main(["compress", str(log), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["original_bytes"] <= 4096


def test_config_explicit_path_beats_env(tmp_path, capsys, monkeypatch):
    log = tmp_path / "tiny.log"
    log.write_text("DEBUG only\n", encoding="utf-8")
    write_config(tmp_path, {"hint": "text", "max_content": 1}, name="wrong.json")
    chosen = write_config(tmp_path, {"hint": "text"}, name="chosen.json")
    monkeypatch.setenv("HEADROOM_CONFIG", str(tmp_path / "wrong.json"))
    assert main(["compress", str(log), "--config", str(chosen), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["hint"] == "text"


def test_missing_default_config_falls_back(tmp_path, capsys, monkeypatch):
    log = tmp_path / "utf8.log"
    log.write_text("INFO fine\n", encoding="utf-8")
    assert main(["compress", str(log), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["compressor"] == "text"


def test_project_backup_used_and_installed_to_home(tmp_path, capsys, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    backup = tmp_path / "backup.json"
    backup.write_text(json.dumps({"encoding": "latin-1", "hint": "log"}), encoding="utf-8")
    monkeypatch.setenv("HEADROOM_PROJECT_CONFIG", str(backup))
    assert main(["compress", str(_latin1_log(tmp_path)), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["compressor"] == "log"
    assert (home / ".headroom" / "config.json").is_file()


def _latin1_log(tmp_path):
    path = tmp_path / "latin1.log"
    path.write_bytes(b"INFO ok\nERROR byte=\xf2\n")
    return path


@pytest.mark.parametrize(
    "payload",
    [
        {"encoding": "not-a-codec"},
        {"max_content": 0},
        {"max_content": True},
        {"max_content": "big"},
        {"hint": "binary"},
        {"unexpected": 1},
    ],
)
def test_invalid_config_values(tmp_path, capsys, monkeypatch, payload):
    log = tmp_path / "app.log"
    log.write_text("INFO fine\n", encoding="utf-8")
    write_config(tmp_path, payload)
    monkeypatch.setenv("HEADROOM_CONFIG", str(tmp_path / "config.json"))
    assert main(["compress", str(log)]) == 2
    error = json.loads(capsys.readouterr().err)
    assert error["error"]["code"] == "CONFIG_ERROR"


def test_bad_json_and_missing_explicit_config(tmp_path, capsys, monkeypatch):
    log = tmp_path / "app.log"
    log.write_text("INFO fine\n", encoding="utf-8")
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("HEADROOM_CONFIG", str(broken))
    assert main(["compress", str(log)]) == 2
    assert json.loads(capsys.readouterr().err)["error"]["code"] == "CONFIG_ERROR"

    assert main(["compress", str(log), "--config", str(tmp_path / "nope.json")]) == 2
    assert json.loads(capsys.readouterr().err)["error"]["code"] == "CONFIG_ERROR"