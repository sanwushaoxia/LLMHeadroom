"""日志压缩器:重复行折叠、堆栈裁剪、低级别过滤和结构化报告。"""

from __future__ import annotations

import re

from headroom.compressors.base import CompressionOutput, Compressor, Omission

_TS = (
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
    r"|\w{3} +\d{1,2} \d{2}:\d{2}:\d{2}"
    r"|\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]"
    r"|\d{2}:\d{2}:\d{2}(?:[.,]\d+)?"
)
# glog: 单字母严重级别紧跟紧凑日期，如 I20260922 13:29:51.721504
_GLOG_TS = r"[IWEF]\d{8} \d{2}:\d{2}:\d{2}(?:\.\d+)?"
_GLOG_SEVERITY = {"I": "INFO", "W": "WARNING", "E": "ERROR", "F": "FATAL"}
_LEVEL = r"(?:TRACE|DEBUG|INFO|NOTICE|WARN(?:ING)?|ERROR|CRITICAL|FATAL|SEVERE)"
_LOG_LINE = re.compile(
    rf"^(?:{_TS})\s+\S+.*|.*\b{_LEVEL}\b\s*[:\-|]|^{_GLOG_TS}\s+\S+.*",
    re.IGNORECASE,
)
_LEVEL_ORDER = {
    "TRACE": 0,
    "DEBUG": 1,
    "INFO": 2,
    "NOTICE": 3,
    "WARN": 4,
    "WARNING": 4,
    "ERROR": 5,
    "CRITICAL": 6,
    "FATAL": 6,
    "SEVERE": 6,
}
_LEVEL_TOKEN = re.compile(rf"\b(?P<level>{_LEVEL})\b", re.IGNORECASE)
_STRUCTURED_LEVEL = re.compile(
    rf"[\"'](?:level|severity)[\"']\s*[:=]\s*[\"'](?P<level>{_LEVEL})[\"']",
    re.IGNORECASE,
)
_FRAME = re.compile(
    r"^\s+(?:File \"|at [\w$.]+(?:\([^)]*\))?|in <module>|caused by:)",
    re.IGNORECASE,
)
_EXC_LINE = re.compile(r"^\s*(?:Traceback|Exception|Error|\w*(?:Error|Exception)\b)", re.IGNORECASE)
_VAR_PARTS = re.compile(
    r"\b\d+(?:\.\d+)?\b|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"|\b(?:\d{1,3}\.){3}\d{1,3}\b|0x[0-9a-f]+",
    re.IGNORECASE,
)


def _extract_level(line: str) -> str | None:
    structured = _STRUCTURED_LEVEL.search(line)
    if structured:
        return structured.group("level").upper()
    # glog 风格：行首单字母严重级别紧跟紧凑日期（I20260922 ...）。
    glog = re.match(r"^\s*([IWEF])\d{8} ", line[:20])
    if glog:
        return _GLOG_SEVERITY[glog.group(1)]
    # Only inspect the first ~100 chars and require a log-like prefix. This prevents
    # a message such as "request body contains ERROR" from becoming the level.
    prefix = line[:100]
    timestamp = re.match(rf"^\s*(?:{_TS})\s+", prefix, re.IGNORECASE)
    if timestamp:
        prefix = prefix[timestamp.end() :]
    prefix = re.sub(r"^\s*\[[^\]]+\]\s*", "", prefix)
    match = re.match(rf"^(?:\S+\s+)?(?P<level>{_LEVEL})(?=\s|[:|\-])", prefix, re.IGNORECASE)
    return match.group("level").upper() if match else None


def _keep(line: str, keep_level: str) -> bool:
    level = _extract_level(line)
    if level is None:
        return True
    threshold = _LEVEL_ORDER.get(keep_level.upper(), _LEVEL_ORDER["WARN"])
    return _LEVEL_ORDER.get(level, 99) >= threshold


def _template(line: str) -> str:
    return _VAR_PARTS.sub("<v>", line).strip()


class LogCompressor(Compressor):
    name = "log"

    def __init__(self, keep_level: str = "WARN", anchor_lines: int = 3):
        if anchor_lines < 0:
            raise ValueError("anchor_lines must be non-negative")
        normalized = keep_level.upper()
        if normalized == "WARNING":
            normalized = "WARN"
        if normalized not in _LEVEL_ORDER:
            raise ValueError(f"unknown keep_level: {keep_level}")
        self.keep_level = normalized
        self.anchor_lines = anchor_lines

    def detect(self, content: str) -> float:
        lines = content.splitlines()
        if len(lines) < 5:
            return 0.0
        sample = lines[:200]
        sample_len = min(len(lines), 200)
        # 多行日志记录的续行（如 glog 打印的配置转储）不是记录头，
        # 但仍属于日志正文；按最近一条记录头延伸，计入命中率。
        hits = 0
        continuation = 0
        for line in sample:
            if _LOG_LINE.match(line) or _extract_level(line):
                hits += 1
                continuation = min(continuation + 1, 5)
            elif continuation:
                hits += 1
                continuation -= 1
        return min(1.0, hits / sample_len * 1.3)

    def compress_with_metadata(self, content: str) -> CompressionOutput:
        lines = content.splitlines()
        out: list[str] = []
        omissions: list[Omission] = []
        i = 0
        omitted_low_level = 0

        def add_omission_note(count: int, start: int, end: int) -> None:
            nonlocal omitted_low_level
            omitted_low_level += count
            note = f"  … [headroom] 省略 {omitted_low_level} 行低级别日志(keep_level={self.keep_level})"
            if out and out[-1].startswith("  … [headroom] 省略"):
                out[-1] = note
            else:
                out.append(note)
            omissions.append(
                Omission(
                    kind="log_level",
                    reason="filtered log lines below keep_level",
                    start=start,
                    end=end,
                    count=count,
                    details={"keep_level": self.keep_level},
                )
            )

        while i < len(lines):
            line = lines[i]
            if _EXC_LINE.match(line) or _FRAME.match(line):
                start = i
                while i < len(lines) and (
                    _FRAME.match(lines[i]) or (i == start and _EXC_LINE.match(lines[i]))
                ):
                    i += 1
                frames = lines[start:i]
                if len(frames) <= 4:
                    out.extend(frames)
                else:
                    out.extend([frames[0], f"  … [headroom] 省略 {len(frames) - 2} 帧堆栈 …", frames[-1]])
                    omissions.append(
                        Omission(
                            kind="stacktrace",
                            reason="trimmed middle stack frames",
                            start=start,
                            end=i,
                            count=len(frames) - 2,
                        )
                    )
                continue

            key = _template(line)
            j = i
            while (
                j < len(lines)
                and not _FRAME.match(lines[j])
                and not _EXC_LINE.match(lines[j])
                and _template(lines[j]) == key
            ):
                j += 1
            run = j - i
            if not _keep(line, self.keep_level):
                add_omission_note(run, i, j)
            elif run >= 3:
                first_ts = _first_ts(lines[i])
                last_ts = _first_ts(lines[j - 1])
                suffix = f"(首 {first_ts} → 末 {last_ts})" if first_ts and last_ts and first_ts != last_ts else ""
                out.append(f"{lines[i]}  [headroom] 同模式 ×{run} 次{suffix}")
                omissions.append(
                    Omission(
                        kind="repeated_log",
                        reason="collapsed repeated log template",
                        start=i,
                        end=j,
                        count=run - 1,
                    )
                )
            else:
                out.extend(lines[i:j])
            i = j

        if self.anchor_lines:
            head = lines[: self.anchor_lines]
            if not all(any(line == output for output in out) for line in head):
                out = ["[headroom] 原始日志头部锚点:", *head, "---", *out]
            if len(lines) > self.anchor_lines:
                tail = lines[-self.anchor_lines :]
                if not all(any(line == output for output in out) for line in tail):
                    out.extend(["---", "[headroom] 原始日志尾部锚点:", *tail])

        return CompressionOutput(
            text="\n".join(out),
            lossy=bool(omissions),
            omissions=tuple(omissions),
            language="log",
        )

    def compress(self, content: str) -> str:
        return self.compress_with_metadata(content).text


def _first_ts(line: str) -> str | None:
    match = re.search(_TS, line)
    return match.group(0) if match else None
