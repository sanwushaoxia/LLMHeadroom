"""纯文本兜底压缩器:空白规整 + 连续重复行折叠。"""

from __future__ import annotations

import re

from headroom.compressors.base import Compressor

_TRAIL_WS = re.compile(r"[ \t]+$")
_EXCESS_BLANK = re.compile(r"\n{3,}")


class TextCompressor(Compressor):
    name = "text"

    def detect(self, content: str) -> float:
        # 兜底压缩器:置信度恒为 0.1,只有在没有更高分压缩器时被选中。
        return 0.1

    def compress(self, content: str) -> str:
        out: list[str] = []
        prev: str | None = None
        run = 1  # 当前行与前一段中相同行的连续出现次数
        for raw in content.splitlines():
            line = _TRAIL_WS.sub("", raw)
            if line == prev:
                run += 1
                continue
            if prev is not None and run >= 2:
                out[-1] = prev + f"  [×{run} 次重复]"
            if not line.strip() and out and not out[-1].strip():
                continue  # 折叠连续空行
            out.append(line)
            prev = line
            run = 1
        if prev is not None and run >= 2:
            out[-1] = prev + f"  [×{run} 次重复]"
        text = "\n".join(out)
        text = _EXCESS_BLANK.sub("\n\n", text)
        return text.strip("\n")
