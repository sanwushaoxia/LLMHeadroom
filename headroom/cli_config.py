"""CLI 专用默认配置。

该模块只负责命令行压缩参数，不混入核心 Headroom Config，也不读取 ZCode MCP 配置。
"""

from __future__ import annotations

import codecs
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from headroom.core.errors import ConfigError

_ALLOWED_HINTS = {"log", "json", "code", "text"}


def _default_config_path() -> Path:
    return Path("~/.headroom/config.json").expanduser()


def _project_backup_path() -> Path:
    """项目仓库内的备份模板；HEADROOM_PROJECT_CONFIG 可覆盖或禁用。"""
    override = os.environ.get("HEADROOM_PROJECT_CONFIG")
    if override is not None:
        return Path(override).expanduser()
    return Path(__file__).resolve().parent.parent / "config.json"


def _load_from_backup() -> CliDefaults | None:
    """读取项目备份 config.json；首次使用时另存到 ~/.headroom/ 作为常驻副本。"""
    backup = _project_backup_path()
    if not backup.is_file():
        return None
    try:
        raw = backup.read_text(encoding="utf-8")
        defaults = _parse(json.loads(raw), backup)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ConfigError):
        return None
    try:
        target = _default_config_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_text(raw, encoding="utf-8")
    except OSError:
        pass
    return defaults


@dataclass(frozen=True)
class CliDefaults:
    encoding: str | None = None
    max_content: int | None = None
    hint: str | None = None


def config_path(explicit: str | None = None) -> tuple[Path, bool]:
    """返回配置路径和是否为显式路径。"""
    if explicit:
        return Path(explicit).expanduser(), True
    configured = os.environ.get("HEADROOM_CONFIG")
    if configured:
        return Path(configured).expanduser(), True
    return _default_config_path(), False


def load_defaults(explicit: str | None = None) -> CliDefaults:
    path, is_explicit = config_path(explicit)
    if not path.exists():
        if is_explicit:
            raise ConfigError(
                f"CLI config file does not exist: {path}",
                details={"path": str(path)},
            )
        return _load_from_backup() or CliDefaults()
    if not path.is_file():
        raise ConfigError(
            f"CLI config path is not a file: {path}",
            details={"path": str(path)},
        )
    try:
        with path.open(encoding="utf-8") as file:
            raw = json.load(file)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigError(
            f"cannot read CLI config: {path}",
            details={"path": str(path), "reason": str(exc)},
        ) from exc
    return _parse(raw, path)


def _parse(raw: Any, path: Path) -> CliDefaults:
    if not isinstance(raw, dict):
        raise ConfigError("CLI config root must be a JSON object", details={"path": str(path)})
    allowed = {"encoding", "max_content", "hint"}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ConfigError(
            "CLI config contains unknown fields",
            details={"path": str(path), "unknown": unknown},
        )

    encoding = raw.get("encoding")
    if encoding is not None:
        if not isinstance(encoding, str) or not encoding.strip():
            raise ConfigError("config encoding must be a non-empty string", details={"path": str(path)})
        encoding = encoding.strip()
        try:
            codecs.lookup(encoding)
        except LookupError as exc:
            raise ConfigError(
                f"unknown text encoding: {encoding}",
                details={"path": str(path), "encoding": encoding},
            ) from exc

    max_content = raw.get("max_content")
    if max_content is not None and (
        isinstance(max_content, bool) or not isinstance(max_content, int) or max_content <= 0
    ):
        raise ConfigError(
            "config max_content must be a positive integer",
            details={"path": str(path)},
        )

    hint = raw.get("hint")
    if hint is not None:
        if not isinstance(hint, str) or hint.strip().lower() not in _ALLOWED_HINTS:
            raise ConfigError(
                "config hint must be one of log, json, code, text or null",
                details={"path": str(path), "hint": hint},
            )
        hint = hint.strip().lower()

    return CliDefaults(encoding=encoding, max_content=max_content, hint=hint)
