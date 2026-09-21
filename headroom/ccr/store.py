"""CCR 持久化存储:SQLite + zlib + TTL + hash + 原子统计。"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
import uuid
import zlib
from pathlib import Path
from typing import Any

from headroom.core.errors import CCRIntegrityError, StoreCapacityError

_SCHEMA_VERSION = 2


class SQLiteStore:
    """跨进程可用的原文存储后端。

    一个实例共享连接并以 RLock 保护；SQLite WAL/busy_timeout 负责跨进程读写协调。
    `stored_bytes` 是压缩 BLOB 的逻辑大小，不等同于数据库文件物理大小。
    """

    def __init__(
        self,
        db_path: Path | str,
        *,
        ttl_seconds: float = 72 * 3600,
        max_store_bytes: int | None = 256 * 1024 * 1024,
        max_store_entries: int | None = 10000,
    ):
        self._db_path = Path(db_path).expanduser()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ttl_seconds = float(ttl_seconds)
        self._max_store_bytes = max_store_bytes
        self._max_store_entries = max_store_entries
        if self._ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be greater than zero")
        if self._max_store_bytes is not None and self._max_store_bytes <= 0:
            raise ValueError("max_store_bytes must be greater than zero or None")
        if self._max_store_entries is not None and self._max_store_entries <= 0:
            raise ValueError("max_store_entries must be greater than zero or None")
        self._lock = threading.RLock()
        self._closed = False
        self._conn = sqlite3.connect(
            str(self._db_path),
            timeout=10.0,
            check_same_thread=False,
            isolation_level=None,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout = 10000")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._migrate()

    def _migrate(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS ccr_entries (
                    ccr_id      TEXT PRIMARY KEY,
                    content_z   BLOB NOT NULL,
                    compressor  TEXT NOT NULL,
                    orig_bytes  INTEGER NOT NULL,
                    created_at  REAL NOT NULL,
                    content_hash TEXT,
                    stored_bytes INTEGER,
                    format_version INTEGER NOT NULL DEFAULT 1
                );
                CREATE INDEX IF NOT EXISTS idx_ccr_created ON ccr_entries (created_at);
                CREATE TABLE IF NOT EXISTS ccr_metrics (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    calls INTEGER NOT NULL DEFAULT 0,
                    ccr_calls INTEGER NOT NULL DEFAULT 0,
                    original_bytes INTEGER NOT NULL DEFAULT 0,
                    emitted_bytes INTEGER NOT NULL DEFAULT 0,
                    original_tokens INTEGER NOT NULL DEFAULT 0,
                    emitted_tokens INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL DEFAULT 0
                );
                INSERT OR IGNORE INTO ccr_metrics (id, updated_at) VALUES (1, 0);
                """
            )
            columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(ccr_entries)")}
            if "content_hash" not in columns:
                self._conn.execute("ALTER TABLE ccr_entries ADD COLUMN content_hash TEXT")
            if "stored_bytes" not in columns:
                self._conn.execute("ALTER TABLE ccr_entries ADD COLUMN stored_bytes INTEGER")
            if "format_version" not in columns:
                self._conn.execute(
                    "ALTER TABLE ccr_entries ADD COLUMN format_version INTEGER NOT NULL DEFAULT 1"
                )
            self._conn.execute(
                "UPDATE ccr_entries SET stored_bytes = length(content_z) WHERE stored_bytes IS NULL"
            )
            self._conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")

    @property
    def ttl_seconds(self) -> float:
        return self._ttl_seconds

    @property
    def db_path(self) -> Path:
        return self._db_path

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._conn.close()
                self._closed = True

    def __enter__(self) -> "SQLiteStore":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _begin_write(self) -> None:
        self._conn.execute("BEGIN IMMEDIATE")

    def _rollback(self) -> None:
        try:
            self._conn.execute("ROLLBACK")
        except sqlite3.OperationalError:
            pass

    def _purge_expired_unlocked(self, now: float) -> int:
        cur = self._conn.execute(
            "DELETE FROM ccr_entries WHERE created_at <= ?", (now - self._ttl_seconds,)
        )
        return cur.rowcount

    def put(self, content: str, compressor: str = "?") -> str:
        """存入原文，返回完整随机 ID。"""
        raw = content.encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        with self._lock:
            self._begin_write()
            try:
                now = time.time()
                self._purge_expired_unlocked(now)
                row = self._conn.execute(
                    "SELECT COUNT(*) AS n, COALESCE(SUM(stored_bytes), 0) AS b FROM ccr_entries"
                ).fetchone()
                payload = zlib.compress(raw)
                count, stored = int(row["n"]), int(row["b"])
                if self._max_store_entries is not None and count + 1 > self._max_store_entries:
                    raise StoreCapacityError("CCR entry limit exceeded", details={"max_store_entries": self._max_store_entries})
                if self._max_store_bytes is not None and stored + len(payload) > self._max_store_bytes:
                    raise StoreCapacityError("CCR storage limit exceeded", details={"max_store_bytes": self._max_store_bytes})
                for _ in range(3):
                    ccr_id = uuid.uuid4().hex
                    try:
                        self._conn.execute(
                            """INSERT INTO ccr_entries
                               (ccr_id, content_z, compressor, orig_bytes, created_at,
                                content_hash, stored_bytes, format_version)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                            (ccr_id, payload, compressor, len(raw), now, digest, len(payload), _SCHEMA_VERSION),
                        )
                        self._conn.execute("COMMIT")
                        return ccr_id
                    except sqlite3.IntegrityError:
                        continue
                raise StoreCapacityError("could not allocate a unique CCR id")
            except Exception:
                self._rollback()
                raise
    def put_with_metrics(
        self,
        content: str,
        compressor: str,
        *,
        original_bytes: int,
        emitted_bytes: int,
        original_tokens: int,
        emitted_tokens: int,
        ccr: bool = True,
    ) -> str:
        """原文写入和累计 metrics 在同一 SQLite 事务完成。"""
        raw = content.encode("utf-8")
        payload = zlib.compress(raw)
        digest = hashlib.sha256(raw).hexdigest()
        with self._lock:
            self._begin_write()
            try:
                now = time.time()
                self._purge_expired_unlocked(now)
                row = self._conn.execute(
                    "SELECT COUNT(*) AS n, COALESCE(SUM(stored_bytes), 0) AS b FROM ccr_entries"
                ).fetchone()
                count, stored = int(row["n"]), int(row["b"])
                if self._max_store_entries is not None and count + 1 > self._max_store_entries:
                    raise StoreCapacityError("CCR entry limit exceeded", details={"max_store_entries": self._max_store_entries})
                if self._max_store_bytes is not None and stored + len(payload) > self._max_store_bytes:
                    raise StoreCapacityError("CCR storage limit exceeded", details={"max_store_bytes": self._max_store_bytes})
                for _ in range(3):
                    ccr_id = uuid.uuid4().hex
                    try:
                        self._conn.execute(
                            """INSERT INTO ccr_entries
                               (ccr_id, content_z, compressor, orig_bytes, created_at,
                                content_hash, stored_bytes, format_version)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                            (ccr_id, payload, compressor, len(raw), now, digest, len(payload), _SCHEMA_VERSION),
                        )
                        self._conn.execute(
                            """UPDATE ccr_metrics SET calls = calls + 1, ccr_calls = ccr_calls + ?,
                               original_bytes = original_bytes + ?, emitted_bytes = emitted_bytes + ?,
                               original_tokens = original_tokens + ?, emitted_tokens = emitted_tokens + ?,
                               updated_at = ? WHERE id = 1""",
                            (1 if ccr else 0, original_bytes, emitted_bytes, original_tokens, emitted_tokens, now),
                        )
                        self._conn.execute("COMMIT")
                        return ccr_id
                    except sqlite3.IntegrityError:
                        continue
                raise StoreCapacityError("could not allocate a unique CCR id")
            except Exception:
                self._rollback()
                raise

    def get(self, ccr_id: str) -> str | None:
        """取回原文；不存在或已过期返回 None，内容损坏抛 CCRIntegrityError。"""
        with self._lock:
            cutoff = time.time() - self._ttl_seconds
            if len(ccr_id) == 8:
                row = self._conn.execute(
                    """SELECT content_z, orig_bytes, content_hash FROM ccr_entries
                       WHERE (ccr_id = ? OR ccr_id LIKE ?) AND created_at > ?
                       ORDER BY created_at DESC LIMIT 1""",
                    (ccr_id, f"{ccr_id}%", cutoff),
                ).fetchone()
            else:
                row = self._conn.execute(
                    """SELECT content_z, orig_bytes, content_hash FROM ccr_entries
                       WHERE ccr_id = ? AND created_at > ?""",
                    (ccr_id, cutoff),
                ).fetchone()
        if row is None:
            return None
        try:
            raw = zlib.decompress(row["content_z"])
        except zlib.error as exc:
            raise CCRIntegrityError("CCR compressed payload is corrupt") from exc
        if len(raw) != row["orig_bytes"] or (
            row["content_hash"] and hashlib.sha256(raw).hexdigest() != row["content_hash"]
        ):
            raise CCRIntegrityError("CCR payload hash or byte length does not match")
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CCRIntegrityError("CCR payload is not valid UTF-8") from exc

    def purge_expired(self) -> int:
        with self._lock:
            self._begin_write()
            try:
                count = self._purge_expired_unlocked(time.time())
                self._conn.execute("COMMIT")
                return count
            except Exception:
                self._rollback()
                raise

    def entry_count(self) -> int:
        with self._lock:
            self._purge_expired_unlocked(time.time())
            row = self._conn.execute("SELECT COUNT(*) AS n FROM ccr_entries").fetchone()
            return int(row["n"])

    def total_orig_bytes(self) -> int:
        with self._lock:
            self._purge_expired_unlocked(time.time())
            row = self._conn.execute("SELECT COALESCE(SUM(orig_bytes), 0) AS b FROM ccr_entries").fetchone()
            return int(row["b"])

    def total_stored_bytes(self) -> int:
        with self._lock:
            self._purge_expired_unlocked(time.time())
            row = self._conn.execute(
                "SELECT COALESCE(SUM(stored_bytes), 0) AS b FROM ccr_entries"
            ).fetchone()
            return int(row["b"])

    def record_metrics(
        self,
        *,
        original_bytes: int,
        emitted_bytes: int,
        original_tokens: int,
        emitted_tokens: int,
        ccr: bool,
    ) -> None:
        with self._lock:
            self._begin_write()
            try:
                self._conn.execute(
                    """UPDATE ccr_metrics SET
                       calls = calls + 1,
                       ccr_calls = ccr_calls + ?,
                       original_bytes = original_bytes + ?,
                       emitted_bytes = emitted_bytes + ?,
                       original_tokens = original_tokens + ?,
                       emitted_tokens = emitted_tokens + ?,
                       updated_at = ?
                       WHERE id = 1""",
                    (
                        1 if ccr else 0,
                        original_bytes,
                        emitted_bytes,
                        original_tokens,
                        emitted_tokens,
                        time.time(),
                    ),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._rollback()
                raise

    def metrics(self) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM ccr_metrics WHERE id = 1").fetchone()
            result = dict(row)
        original = int(result["original_tokens"])
        result["saved_ratio"] = 0.0 if original == 0 else max(
            0.0, min(1.0, 1 - int(result["emitted_tokens"]) / original)
        )
        return result
