"""CCR 原文存储:SQLite + zlib,TTL 惰性清理,幂等建表。"""

from __future__ import annotations

import sqlite3
import time
import uuid
import zlib
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ccr_entries (
    ccr_id      TEXT PRIMARY KEY,
    content_z   BLOB NOT NULL,
    compressor  TEXT NOT NULL,
    orig_bytes  INTEGER NOT NULL,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ccr_created ON ccr_entries (created_at);
"""


class SQLiteStore:
    """原文存储后端。进程/机器重启后 LLM 仍可凭 ccr_id 取回原文。"""

    def __init__(self, db_path: Path | str):
        self._db_path = Path(db_path).expanduser()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def put(self, content: str, compressor: str = "?") -> str:
        """存入原文,返回 8 位 ccr_id。写入时惰性清理过期条目。"""
        ccr_id = uuid.uuid4().hex[:8]
        self._conn.execute(
            "INSERT INTO ccr_entries VALUES (?, ?, ?, ?, ?)",
            (ccr_id, zlib.compress(content.encode("utf-8")), compressor, len(content), time.time()),
        )
        self._conn.commit()
        return ccr_id

    def get(self, ccr_id: str) -> str | None:
        """取回原文;不存在或已过期返回 None。"""
        row = self._conn.execute(
            "SELECT content_z FROM ccr_entries WHERE ccr_id = ? AND created_at > ?",
            (ccr_id, time.time() - self.ttl_seconds),
        ).fetchone()
        if row is None:
            return None
        return zlib.decompress(row[0]).decode("utf-8")

    def purge_expired(self) -> int:
        """删除过期条目,返回删除数量。"""
        cur = self._conn.execute(
            "DELETE FROM ccr_entries WHERE created_at <= ?", (time.time() - self.ttl_seconds,)
        )
        self._conn.commit()
        return cur.rowcount

    def entry_count(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM ccr_entries WHERE created_at > ?",
            (time.time() - self.ttl_seconds,),
        ).fetchone()
        return int(row[0])

    def total_orig_bytes(self) -> int:
        """库存条目对应的原文总字节数(用于 stats 展示)。"""
        row = self._conn.execute(
            "SELECT COALESCE(SUM(orig_bytes), 0) FROM ccr_entries WHERE created_at > ?",
            (time.time() - self.ttl_seconds,),
        ).fetchone()
        return int(row[0])

    @property
    def ttl_seconds(self) -> float:
        from headroom.core.models import Config

        return Config().ttl_hours * 3600.0
