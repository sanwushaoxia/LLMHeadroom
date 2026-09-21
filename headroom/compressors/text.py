"""纯文本兜底压缩器:保守处理空白和连续重复行。"""

from __future__ import annotations

import re

from headroom.compressors.base import CompressionOutput, Compressor, Omission

_TRAIL_WS = re.compile(r"[ \t]+$")


class TextCompressor(Compressor):
    name = "text"

    def detect(self, content: str) -> float:
        return 0.1

    def compress_with_metadata(self, content: str) -> CompressionOutput:
        lines = content.splitlines(keepends=False)
        out: list[str] = []
        omissions: list[Omission] = []
        prev: str | None = None
        run_start = 0
        run = 0
        whitespace_changed = False

        def flush() -> None:
            nonlocal run, prev, run_start
            if prev is None:
                return
            if run >= 2:
                out.append(prev + f"  [×{run} 次重复]")
                omissions.append(
                    Omission(
                        kind="repeated_lines",
                        reason=f"collapsed {run} repeated lines",
                        start=run_start,
                        end=run_start + run,
                        count=run - 1,
                    )
                )
            elif prev == "":
                # 空行永远按空白处理，不生成重复行提示。
                if not out or out[-1] != "":
                    out.append("")
            else:
                out.append(prev)
            run = 0
            prev = None

        for index, raw in enumerate(lines):
            line = _TRAIL_WS.sub("", raw)
            whitespace_changed |= line != raw
            if line == "":
                flush()
                if out and out[-1] == "":
                    omissions.append(
                        Omission(
                            kind="blank_lines",
                            reason="collapsed consecutive blank lines",
                            start=index,
                            end=index + 1,
                            count=1,
                        )
                    )
                    continue
                out.append("")
                continue
            if prev is None:
                prev, run_start, run = line, index, 1
            elif line == prev:
                run += 1
            else:
                flush()
                prev, run_start, run = line, index, 1
        flush()

        # 保持正文内容，不再无条件 strip 首尾换行。
        text = "\n".join(out)
        if whitespace_changed:
            omissions.append(Omission(kind="whitespace", reason="trimmed trailing spaces"))
        return CompressionOutput(
            text=text,
            lossy=bool(omissions),
            omissions=tuple(omissions),
            language="text",
        )

    def compress(self, content: str) -> str:
        return self.compress_with_metadata(content).text
