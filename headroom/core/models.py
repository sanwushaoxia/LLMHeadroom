"""Headroom 核心数据模型与配置。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    return float(value) if value is not None else default


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return int(value) if value is not None else default


@dataclass
class Config:
    """全局配置,所有字段可用环境变量覆盖。

    - HEADROOM_DB:          SQLite 路径(默认 ~/.headroom/ccr.db)
    - HEADROOM_TTL_HOURS:   CCR 条目存活时间(默认 72h)
    - HEADROOM_MIN_RATIO:   触发 CCR 的最低压缩率(默认 0.3)
    - HEADROOM_MAX_CONTENT: 单次输入最大字节数(默认 2MB)
    - HEADROOM_MAX_LINES:   大输出截断保留的行数(默认 400)
    """

    db_path: Path = field(
        default_factory=lambda: Path(
            os.environ.get("HEADROOM_DB", "~/.headroom/ccr.db")
        ).expanduser()
    )
    ttl_hours: float = field(default_factory=lambda: _env_float("HEADROOM_TTL_HOURS", 72.0))
    min_ratio: float = field(default_factory=lambda: _env_float("HEADROOM_MIN_RATIO", 0.3))
    max_content: int = field(default_factory=lambda: _env_int("HEADROOM_MAX_CONTENT", 2 * 1024 * 1024))
    max_lines: int = field(default_factory=lambda: _env_int("HEADROOM_MAX_LINES", 400))


@dataclass
class Stats:
    """一次压缩的统计信息。"""

    original_bytes: int
    compressed_bytes: int
    original_tokens: int
    compressed_tokens: int
    compressor: str
    ccr_id: str | None = None

    @property
    def saved_ratio(self) -> float:
        """节省比例 0~1;原样返回时为 0。"""
        if self.original_bytes == 0:
            return 0.0
        return 1.0 - self.compressed_bytes / self.original_bytes


@dataclass
class CompressResult:
    """compress() 的完整结果:最终文本 + 统计 + 原文是否可取回。"""

    text: str
    stats: Stats
    retrievable: bool = False


def estimate_tokens(text: str) -> int:
    """粗略 token 估算(len/4 启发式),不引入 tokenizer 依赖。"""
    return max(1, (len(text) + 3) // 4)
