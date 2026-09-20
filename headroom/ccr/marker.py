"""CCR 标记:嵌入压缩结果头部,LLM 可凭 id 调 headroom_retrieve 取回原文。"""

from __future__ import annotations

import re

MARKER_RE = re.compile(
    r"\[headroom:ccr://(?P<id>[0-9a-f]{8})\|orig=(?P<orig>\d+)B\|saved=(?P<saved>\d+)%\]"
)

# 压缩结果首行模板:
#   [headroom:ccr://a1b2c3d4|orig=12580B|saved=91%]
#   LLM 可调用 headroom_retrieve(ccr_id="a1b2c3d4") 随时取回原文。
_MARKET_TEMPLATE = (
    "[headroom:ccr://{ccr_id}|orig={orig_bytes}B|saved={saved_pct}%]"
    " 原文已压缩存档,可调用 headroom_retrieve(ccr_id=\"{ccr_id}\") 取回完整原文。"
)


def build_marker(ccr_id: str, orig_bytes: int, saved_pct: int) -> str:
    return _MARKET_TEMPLATE.format(ccr_id=ccr_id, orig_bytes=orig_bytes, saved_pct=saved_pct)


def embed(marker: str, compressed: str) -> str:
    """把 CCR 标记放到压缩文本首行(单独成行,不干扰正文)。"""
    return marker + "\n" + compressed


def parse(text: str) -> tuple[str, int, int] | None:
    """从文本解析 (ccr_id, orig_bytes, saved_pct);无标记返回 None。"""
    m = MARKER_RE.search(text)
    if m is None:
        return None
    return m.group("id"), int(m.group("orig")), int(m.group("saved"))


def extract_id(text: str) -> str | None:
    parsed = parse(text)
    return parsed[0] if parsed else None
