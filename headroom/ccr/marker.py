"""严格、可兼容的 CCR marker。"""

from __future__ import annotations

import re
from dataclasses import dataclass

_MARKER_RE = re.compile(
    r"^\[headroom:ccr://(?P<id>[0-9a-fA-F]{8,64})\|"
    r"orig=(?P<orig>\d+)B\|saved=(?P<saved>\d+)%"
    r"(?:\|h=(?P<hash>[0-9a-fA-F]{64}))?\]"
    r"(?: .*)?$"
)


@dataclass(frozen=True)
class Marker:
    ccr_id: str
    original_bytes: int
    saved_pct: int
    content_hash: str | None = None


def build_marker(
    ccr_id: str,
    orig_bytes: int,
    saved_pct: int,
    content_hash: str | None = None,
) -> str:
    if not re.fullmatch(r"[0-9a-fA-F]{8,64}", ccr_id):
        raise ValueError("ccr_id must be 8-64 hexadecimal characters")
    if orig_bytes < 0:
        raise ValueError("orig_bytes must be non-negative")
    if not 0 <= saved_pct <= 100:
        raise ValueError("saved_pct must be between 0 and 100")
    suffix = ""
    if content_hash is not None:
        if not re.fullmatch(r"[0-9a-fA-F]{64}", content_hash):
            raise ValueError("content_hash must be a SHA-256 hex digest")
        suffix = f"|h={content_hash.lower()}"
    return (
        f"[headroom:ccr://{ccr_id.lower()}|orig={orig_bytes}B|saved={saved_pct}%{suffix}]"
        f" 原文已压缩存档,可调用 headroom_retrieve(ccr_id=\"{ccr_id.lower()}\") 取回完整原文。"
    )


def embed(marker: str, compressed: str) -> str:
    return marker + "\n" + compressed


def parse_details(text: str) -> Marker | None:
    """只解析首行 marker，避免正文中的伪 marker 被误识别。"""
    first_line = text.splitlines()[0] if text else ""
    match = _MARKER_RE.match(first_line)
    if match is None:
        return None
    original_bytes = int(match.group("orig"))
    saved_pct = int(match.group("saved"))
    if not 0 <= saved_pct <= 100:
        return None
    return Marker(
        ccr_id=match.group("id").lower(),
        original_bytes=original_bytes,
        saved_pct=saved_pct,
        content_hash=match.group("hash").lower() if match.group("hash") else None,
    )


def parse(text: str) -> tuple[str, int, int] | None:
    marker = parse_details(text)
    return None if marker is None else (marker.ccr_id, marker.original_bytes, marker.saved_pct)


def extract_id(text: str) -> str | None:
    marker = parse_details(text)
    return marker.ccr_id if marker else None
