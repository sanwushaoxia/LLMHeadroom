"""P0/P1/P2 边界测试。"""

from __future__ import annotations

import json
import threading

import pytest

from headroom.ccr.marker import extract_id, parse_details
from headroom.ccr.store import SQLiteStore
from headroom.core.errors import ConfigError, ContentTooLargeError, UnknownHintError
from headroom.core.models import Config, utf8_bytes
from headroom.core.pipeline import Headroom
from headroom.compressors.json import JsonCompressor
from headroom.compressors.log import LogCompressor
from headroom.compressors.text import TextCompressor


def test_utf8_size_and_stats(tmp_path):
    content = "中文🙂\n" * 300
    assert utf8_bytes(content) > len(content)
    engine = Headroom(Config(db_path=tmp_path / "unicode.db", max_content=utf8_bytes(content) + 1))
    try:
        result = engine.compress(content, hint="text")
        assert result.stats.original_bytes == utf8_bytes(content)
        assert parse_details(result.text) is None or parse_details(result.text).original_bytes == utf8_bytes(content)
    finally:
        engine.close()


def test_oversize_is_rejected_without_truncation(tmp_path):
    engine = Headroom(Config(db_path=tmp_path / "limit.db", max_content=5))
    try:
        with pytest.raises(ContentTooLargeError) as exc:
            engine.compress("中文")
        assert exc.value.actual_bytes == len("中文".encode())
        assert engine.stats()["calls"] == 0
    finally:
        engine.close()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"min_ratio": -0.1},
        {"min_ratio": 1.1},
        {"ttl_hours": 0},
        {"ttl_hours": float("nan")},
        {"max_content": 0},
        {"max_store_bytes": 0},
    ],
)
def test_config_validation(kwargs):
    with pytest.raises(ConfigError):
        Config(**kwargs)


def test_hint_route_and_unknown_hint(tmp_path):
    engine = Headroom(Config(db_path=tmp_path / "hint.db"))
    try:
        assert engine.compress("a\na\na", hint="text").stats.compressor == "text"
        with pytest.raises(UnknownHintError):
            engine.compress("anything", hint="does-not-exist")
    finally:
        engine.close()


def test_custom_ttl_is_honored(tmp_path):
    store = SQLiteStore(tmp_path / "ttl.db", ttl_seconds=0.01)
    cid = store.put("content")
    import time

    time.sleep(0.03)
    assert store.get(cid) is None
    store.close()


def test_persistent_stats_survive_reopen(tmp_path):
    path = tmp_path / "metrics.db"
    first = Headroom(Config(db_path=path))
    first.compress("tiny")
    first.close()
    second = Headroom(Config(db_path=path))
    try:
        assert second.stats()["calls"] == 1
    finally:
        second.close()


def test_store_hash_corruption_detected(tmp_path):
    store = SQLiteStore(tmp_path / "hash.db")
    cid = store.put("content")
    store._conn.execute("UPDATE ccr_entries SET content_hash = ? WHERE ccr_id = ?", ("0" * 64, cid))
    with pytest.raises(Exception) as exc:
        store.get(cid)
    assert "hash" in str(exc.value).lower()
    store.close()


def test_store_capacity_is_atomic(tmp_path):
    store = SQLiteStore(tmp_path / "capacity.db", max_store_bytes=1)
    with pytest.raises(Exception):
        store.put("content")
    assert store.entry_count() == 0
    store.close()


def test_marker_is_strictly_first_line():
    assert extract_id("body\n[headroom:ccr://deadbeef|orig=1B|saved=1%]") is None


def test_text_blank_lines_are_not_repeat_marked():
    result = TextCompressor().compress("a\n\n\n\nb")
    assert "重复" not in result
    assert "\n\n" in result


def test_log_message_error_does_not_change_level():
    content = "\n".join(
        [f"2024-01-01 00:00:{i:02d} INFO request body contains ERROR" for i in range(20)]
    )
    output = LogCompressor(keep_level="WARN").compress(content)
    assert "低级别日志" in output


def test_log_anchor_zero_does_not_restore_all():
    content = "\n".join(f"2024-01-01 00:00:{i:02d} DEBUG x" for i in range(20))
    output = LogCompressor(anchor_lines=0).compress(content)
    assert len(output.splitlines()) < len(content.splitlines())


def test_json_missing_field_and_deep_list():
    payload = json.dumps([{"id": 1}, {"id": 2, "optional": {"a": 1}}, {"id": 3}])
    output = JsonCompressor().compress(payload)
    assert json.loads(output)
    deep: object = 1
    for _ in range(30):
        deep = [deep]
    result = JsonCompressor(max_depth=4).compress(json.dumps(deep))
    assert json.loads(result)


def test_concurrent_store_writes(tmp_path):
    store = SQLiteStore(tmp_path / "concurrent.db", max_store_entries=100)
    ids: list[str] = []
    failures: list[Exception] = []
    lock = threading.Lock()

    def worker() -> None:
        try:
            cid = store.put("x" * 100)
            with lock:
                ids.append(cid)
        except Exception as exc:  # pragma: no cover - failure is asserted below
            with lock:
                failures.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not failures
    assert len(set(ids)) == 20
    store.close()
