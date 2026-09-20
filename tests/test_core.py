"""TextCompressor / ContentRouter / CCR / pipeline 测试。"""

import time

import pytest

from headroom.ccr.marker import build_marker, embed, extract_id, parse
from headroom.ccr.store import SQLiteStore
from headroom.compressors.text import TextCompressor
from headroom.core.models import Config
from headroom.core.pipeline import Headroom
from headroom.core.router import ContentRouter


# --------------------------------------------------------------- Text


class TestTextCompressor:
    def test_folds_repeated_lines(self):
        out = TextCompressor().compress("a\na\na\nb")
        assert "×3" in out and "b" in out

    def test_collapses_blank_runs(self):
        out = TextCompressor().compress("a\n\n\n\n\nb")
        assert "\n\n\n" not in out

    def test_short_content_unchanged(self):
        assert TextCompressor().compress("hello\nworld") == "hello\nworld"


# --------------------------------------------------------------- Router


class TestRouter:
    def test_routes_json(self):
        r = ContentRouter()
        assert r.select('{"a": [1,2,3]}').name == "json"

    def test_routes_log(self):
        log = "\n".join(f"2024-01-01 00:00:{i:02d} INFO ok {i}" for i in range(30))
        assert r_.select(log).name == "log"

    def test_routes_code(self):
        code = "import os\ndef f():\n    return 1\nclass A:\n    pass\n" * 3
        assert r_.select(code).name == "code"

    def test_falls_back_to_text(self):
        assert r_.select("just some plain prose words here\nnothing special").name == "text"

    def test_custom_compressor_wins(self):
        from headroom.compressors.base import Compressor

        class Always(Compressor):
            name = "always"

            def detect(self, content):
                return 0.99

            def compress(self, content):
                return "X"

        r = ContentRouter()
        r.register(Always())
        assert r.select("anything").name == "always"


r_ = ContentRouter()


# --------------------------------------------------------------- CCR


class TestStore:
    def test_roundtrip(self, tmp_path):
        s = SQLiteStore(tmp_path / "t.db")
        cid = s.put("hello 世界 " * 100)
        assert s.get(cid) == "hello 世界 " * 100
        s.close()

    def test_unknown_id_returns_none(self, tmp_path):
        s = SQLiteStore(tmp_path / "t.db")
        assert s.get("deadbeef") is None
        s.close()

    def test_persistence_across_reopen(self, tmp_path):
        s1 = SQLiteStore(tmp_path / "t.db")
        cid = s1.put("persistent content")
        s1.close()
        s2 = SQLiteStore(tmp_path / "t.db")
        assert s2.get(cid) == "persistent content"
        s2.close()

    def test_ttl_expiry(self, tmp_path):
        s = SQLiteStore(tmp_path / "t.db")
        cid = s.put("old content")
        # 手工把 created_at 拨到 100 小时前(默认 TTL 72h)
        old = time.time() - 100 * 3600
        s._conn.execute("UPDATE ccr_entries SET created_at = ?", (old,))
        s._conn.commit()
        assert s.get(cid) is None
        assert s.purge_expired() == 1
        s.close()


class TestMarker:
    def test_build_parse_roundtrip(self):
        m = build_marker("a1b2c3d4", 12580, 91)
        assert parse(m) == ("a1b2c3d4", 12580, 91)
        assert extract_id(m) == "a1b2c3d4"

    def test_embed_puts_marker_on_first_line(self):
        text = embed("MARKER", "body")
        assert text.splitlines()[0] == "MARKER"
        assert text.splitlines()[1] == "body"

    def test_no_marker_in_plain_text(self):
        assert extract_id("no marker here") is None


# --------------------------------------------------------------- Pipeline


class TestPipeline:
    def test_compress_big_log_stores_ccr(self, hr):
        log = "\n".join(f"2024-01-01 00:{i // 60:02d}:{i % 60:02d} DEBUG health ok {i}" for i in range(300))
        result = hr.compress(log)
        assert result.retrievable
        assert result.stats.ccr_id
        assert extract_id(result.text) == result.stats.ccr_id
        assert result.stats.saved_ratio > 0.5

    def test_roundtrip_exact(self, hr):
        log = "\n".join(f"2024-01-01 00:{i // 60:02d}:{i % 60:02d} DEBUG health ok {i}" for i in range(300))
        result = hr.compress(log)
        assert hr.retrieve(result.stats.ccr_id) == log

    def test_small_content_not_ccred(self, hr):
        result = hr.compress("short line")
        assert not result.retrievable
        assert result.text == "short line"

    def test_min_ratio_env(self, tmp_path):
        cfg = Config(db_path=tmp_path / "x.db", min_ratio=0.99)
        hr2 = Headroom(config=cfg)
        try:
            # 只省约 90% 达不到 99% 门槛 → 不走 CCR
            content = "\n".join(f"2024-01-01 00:{i // 60:02d}:{i % 60:02d} DEBUG health ok {i}" for i in range(300))
            result = hr2.compress(content)
            assert not result.retrievable
        finally:
            hr2.close()

    def test_preview_mode(self, hr):
        log = "\n".join(f"2024-01-01 00:{i // 60:02d}:{i % 60:02d} DEBUG health ok {i}" for i in range(300))
        cid = hr.compress(log).stats.ccr_id
        preview = hr.retrieve(cid, mode="preview", max_chars=50)
        assert len(preview) == 50

    def test_stats_accumulate(self, hr):
        log = "\n".join(f"2024-01-01 00:{i // 60:02d}:{i % 60:02d} DEBUG health ok {i}" for i in range(300))
        hr.compress(log)
        hr.compress("tiny")
        st = hr.stats()
        assert st["calls"] == 2
        assert st["ccr_entries"] == 1
        assert 0 < st["saved_ratio"] < 1
