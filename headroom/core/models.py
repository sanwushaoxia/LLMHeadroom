"""Headroom 核心数据模型、配置和统一尺寸口径。"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from headroom.core.errors import ConfigError


def utf8_bytes(text: str) -> int:
    """返回文本编码为 UTF-8 后的实际字节数。"""
    return len(text.encode("utf-8"))


def _env_value(name: str) -> str | None:
    return os.environ.get(name)


def _env_float(name: str, default: float) -> float:
    value = _env_value(name)
    if value is None:
        return default
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} must be a number", details={"variable": name}) from exc
    return result


def _env_int(name: str, default: int) -> int:
    value = _env_value(name)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} must be an integer", details={"variable": name}) from exc


@dataclass
class Config:
    """全局配置，字段可用环境变量覆盖。

    `max_content` 和存储容量均按 UTF-8 字节计算；超限默认拒绝，避免静默丢失原文。
    """

    db_path: Path = field(
        default_factory=lambda: Path(
            os.environ.get("HEADROOM_DB", "~/.headroom/ccr.db")
        ).expanduser()
    )
    ttl_hours: float = field(default_factory=lambda: _env_float("HEADROOM_TTL_HOURS", 72.0))
    min_ratio: float = field(default_factory=lambda: _env_float("HEADROOM_MIN_RATIO", 0.3))
    max_content: int = field(
        default_factory=lambda: _env_int("HEADROOM_MAX_CONTENT", 2 * 1024 * 1024)
    )
    max_store_bytes: int | None = field(
        default_factory=lambda: _env_int("HEADROOM_MAX_STORE_BYTES", 256 * 1024 * 1024)
    )
    max_store_entries: int | None = field(
        default_factory=lambda: _env_int("HEADROOM_MAX_STORE_ENTRIES", 10000)
    )
    retrieve_default_limit: int = field(
        default_factory=lambda: _env_int("HEADROOM_RETRIEVE_DEFAULT_LIMIT", 20000)
    )

    def __post_init__(self) -> None:
        self.db_path = Path(self.db_path).expanduser()
        if not math.isfinite(self.ttl_hours) or self.ttl_hours <= 0:
            raise ConfigError("ttl_hours must be finite and greater than zero")
        if not math.isfinite(self.min_ratio) or not 0 <= self.min_ratio <= 1:
            raise ConfigError("min_ratio must be between 0 and 1")
        for name in ("max_content", "retrieve_default_limit"):
            value = getattr(self, name)
            if value <= 0:
                raise ConfigError(f"{name} must be greater than zero")
        for name in ("max_store_bytes", "max_store_entries"):
            value = getattr(self, name)
            if value is not None and value <= 0:
                raise ConfigError(f"{name} must be greater than zero or None")


@dataclass
class Stats:
    """一次压缩的最终输出统计。

    `compressed_bytes` 是压缩正文，不含 CCR marker；`emitted_bytes` 是交给 Agent 的
    文本大小，包含 marker。MCP footer 不属于核心统计。
    """

    original_bytes: int
    compressed_bytes: int
    emitted_bytes: int
    original_tokens: int
    compressed_tokens: int
    emitted_tokens: int
    compressor: str
    ccr_id: str | None = None
    retrievable: bool = False
    hint: str | None = None
    warnings: list[str] = field(default_factory=list)
    lossy: bool = False
    omissions: list[dict[str, Any]] = field(default_factory=list)

    @property
    def saved_ratio(self) -> float:
        if self.original_bytes <= 0:
            return 0.0
        return max(0.0, min(1.0, 1.0 - self.emitted_bytes / self.original_bytes))

    def as_dict(self) -> dict[str, Any]:
        return {
            "original_bytes": self.original_bytes,
            "compressed_bytes": self.compressed_bytes,
            "emitted_bytes": self.emitted_bytes,
            "original_tokens": self.original_tokens,
            "compressed_tokens": self.compressed_tokens,
            "emitted_tokens": self.emitted_tokens,
            "compressor": self.compressor,
            "ccr_id": self.ccr_id,
            "retrievable": self.retrievable,
            "hint": self.hint,
            "saved_ratio": round(self.saved_ratio, 6),
            "warnings": list(self.warnings),
            "lossy": self.lossy,
            "omissions": list(self.omissions),
        }


@dataclass
class CompressResult:
    """compress() 的完整结果。"""

    text: str
    stats: Stats
    retrievable: bool = False
    warnings: list[str] = field(default_factory=list)


def estimate_tokens(text: str) -> int:
    """粗略 token 估算(len/4 启发式)，只用于相对统计，不等同字节数。"""
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)
