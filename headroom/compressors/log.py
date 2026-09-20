"""日志压缩器:重复行折叠 + 堆栈跟踪裁剪 + 低级别日志过滤。"""

from __future__ import annotations

import re

from headroom.compressors.base import Compressor

# 常见日志行首:ISO 时间戳 / syslog / log4j 风格 / 仅时间(HH:MM:SS.ffffff)
_TS = (
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"  # ISO
    r"|\w{3} +\d{1,2} \d{2}:\d{2}:\d{2}"                                            # syslog
    r"|\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]"                                     # [2024-01-01 00:00:00]
    r"|\d{2}:\d{2}:\d{2}(?:[.,]\d+)?"                                               # 21:20:53.091393
)
_LEVEL = r"(?:TRACE|DEBUG|INFO|NOTICE|WARN(?:ING)?|ERROR|CRITICAL|FATAL|SEVERE)"
_LOG_LINE = re.compile(rf"^(?:{_TS})\s+\S+.*|.*\b{_LEVEL}\b\s*[:\-|]", re.IGNORECASE)

# 级别分级:低于 keep_level 的行在压缩时被过滤
_LEVEL_ORDER = {
    "TRACE": 0, "DEBUG": 1, "INFO": 2, "NOTICE": 3, "WARN": 4, "WARNING": 4,
    "ERROR": 5, "CRITICAL": 6, "FATAL": 6, "SEVERE": 6,
}
_LEVEL_RE = re.compile(rf"\b({_LEVEL})\b", re.IGNORECASE)

# 堆栈帧:Python "  File ..." / Java "\tat ..." / 通用 "  at ..." / "caused by"
_FRAME = re.compile(r"^\s+(?:File \"|at [\w$.]+(?:\([^)]*\))?|in <module>|caused by:)", re.IGNORECASE)
_EXC_LINE = re.compile(r"^\s*(?:Traceback|Exception|Error|\w*(?:Error|Exception)\b)", re.IGNORECASE)

# 同一消息模板的重复:去掉数字/uuid/地址等可变部分后比较
_VAR_PARTS = re.compile(
    r"\b\d+(?:\.\d+)?\b|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"|\b(?:\d{1,3}\.){3}\d{1,3}\b|0x[0-9a-f]+",
    re.IGNORECASE,
)


def _keep(line: str, keep_level: str) -> bool:
    """行是否达到保留级别;无级别标签的行视为重要(保留)。"""
    m = _LEVEL_RE.search(line)
    if m is None:
        return True
    level = m.group(1).upper()
    if level == "WARNING":
        level = "WARN"
    threshold = _LEVEL_ORDER.get(keep_level.upper(), 4)
    return _LEVEL_ORDER.get(level, 99) >= threshold


def _template(line: str) -> str:
    """提取消息模板:去掉可变部分,用于识别重复模式。"""
    return _VAR_PARTS.sub("<v>", line).strip()


class LogCompressor(Compressor):
    name = "log"

    def __init__(self, keep_level: str = "WARN", anchor_lines: int = 3):
        self.keep_level = keep_level
        self.anchor_lines = anchor_lines

    def detect(self, content: str) -> float:
        lines = content.splitlines()
        if len(lines) < 5:
            return 0.0
        hits = sum(1 for ln in lines[:200] if _LOG_LINE.match(ln))
        ratio = hits / min(len(lines), 200)
        return min(1.0, ratio * 1.3)

    def compress(self, content: str) -> str:
        lines = content.splitlines()
        n = len(lines)
        out: list[str] = []
        i = 0

        def add_omission_note(count: int) -> None:
            note = f"  … [headroom] 省略 {count} 行低级别日志(keep_level={self.keep_level})"
            if out and "行低级别日志" in out[-1]:
                prev = int(out[-1].split("省略 ")[1].split(" 行")[0])
                out[-1] = f"  … [headroom] 省略 {prev + count} 行低级别日志(keep_level={self.keep_level})"
            else:
                out.append(note)

        while i < n:
            line = lines[i]
            # 1) 堆栈跟踪:异常行 + 连续帧
            if _EXC_LINE.match(line) or _FRAME.match(line):
                start = i
                while i < n and (_FRAME.match(lines[i]) or (i == start and _EXC_LINE.match(lines[i]))):
                    i += 1
                frames = lines[start:i]
                if len(frames) <= 4:
                    out.extend(frames)
                else:
                    out.append(frames[0])
                    out.append(f"  … [headroom] 省略 {len(frames) - 2} 帧堆栈 …")
                    out.append(frames[-1])
                continue
            # 2) 先按消息模板折叠连续重复(无论级别),再决定整组去留
            key = _template(line)
            j = i
            while (
                j < n
                and not _FRAME.match(lines[j])
                and not _EXC_LINE.match(lines[j])
                and _template(lines[j]) == key
            ):
                j += 1
            run = j - i
            if not _keep(line, self.keep_level):
                add_omission_note(run)
            elif run >= 3:
                first_ts = _first_ts(lines[i])
                last_ts = _first_ts(lines[j - 1])
                suffix = f"(首 {first_ts} → 末 {last_ts})" if first_ts and last_ts and first_ts != last_ts else ""
                out.append(f"{lines[i]}  [headroom] 同模式 ×{run} 次{suffix}")
            else:
                out.extend(lines[i:j])
            i = j

        # 3) 头尾锚点:被过滤掉的首尾内容强制保留,保证 LLM 能看到日志起止
        head = lines[: self.anchor_lines]
        if not all(any(ln == o for o in out) for ln in head):
            out = ["[headroom] 原始日志头部锚点:", *head, "---", *out]
        if n > self.anchor_lines:
            tail = lines[-self.anchor_lines :]
            if not all(any(ln == o for o in out) for ln in tail):
                out.extend(["---", "[headroom] 原始日志尾部锚点:", *tail])
        return "\n".join(out)


def _first_ts(line: str) -> str | None:
    m = re.search(_TS, line)
    return m.group(0) if m else None
