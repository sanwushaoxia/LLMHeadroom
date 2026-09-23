"""Lossless conversion of plain-text logs to structured JSON records."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Literal, TextIO


_TIMESTAMP = (
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
    r"|\w{3} +\d{1,2} \d{2}:\d{2}:\d{2}"
    r"|\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]"
    r"|\d{2}:\d{2}:\d{2}(?:[.,]\d+)?"
)
_LEVEL = r"(?:TRACE|DEBUG|INFO|NOTICE|WARN(?:ING)?|ERROR|CRITICAL|FATAL|SEVERE)"
# glog: 单字母严重级别紧跟紧凑日期，如 I20260922 13:29:51.721504
_GLOG_PREFIX = re.compile(
    r"^\s*(?P<severity>[IWEF])(?P<timestamp>\d{8} \d{2}:\d{2}:\d{2}(?:\.\d+)?)\s+"
    r"(?P<thread_id>\d+)\s+(?P<source_file>[^:\s]+):(?P<source_line>\d+)\]\s*(?P<message>.*)$",
)
_GLOG_SEVERITY = {"I": "INFO", "W": "WARNING", "E": "ERROR", "F": "FATAL"}
_LOG_PREFIX = re.compile(
    rf"^\s*(?P<timestamp>{_TIMESTAMP})\s+"
    rf"(?P<level>{_LEVEL})(?=\s|[:|=-])\s*(?P<rest>.*)$",
    re.IGNORECASE,
)
_TIMESTAMP_PREFIX = re.compile(rf"^\s*(?:{_TIMESTAMP})(?=\s|$)", re.IGNORECASE)
_CONTEXT = re.compile(r"^\[(?P<context>[^\]]*)\](?P<tags>(?:\[[^\]]*\])*)\s*")
_PID = re.compile(r"^pid(?P<process_id>\d+)(?:/(?P<service>.*))?$", re.IGNORECASE)
_THREAD = re.compile(r"^tid=(?P<thread_id>\S+)(?:\s+|$)", re.IGNORECASE)
_SOURCE = re.compile(r"^(?P<source_file>\S+):(?P<source_line>\d+)(?:\s+|$)")


@dataclass(frozen=True)
class ParsedLogLine:
    """Structured fields parsed from one timestamped log line."""

    timestamp: str
    level: str
    process_id: int | None
    service: str | None
    thread_id: int | str | None
    source_file: str | None
    source_line: int | None
    message: str
    raw: str
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LogRecord:
    """A parsed log record including its source line range and continuations."""

    timestamp: str
    level: str
    process_id: int | None
    service: str | None
    thread_id: int | str | None
    source_file: str | None
    source_line: int | None
    message: str
    line_start: int
    line_end: int
    continuation: tuple[str, ...] = ()
    raw: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self, *, include_raw: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "type": "log",
            "timestamp": self.timestamp,
            "level": self.level,
            "process_id": self.process_id,
            "service": self.service,
            "thread_id": self.thread_id,
            "source_file": self.source_file,
            "source_line": self.source_line,
            "message": self.message,
            "continuation": list(self.continuation),
            "line_start": self.line_start,
            "line_end": self.line_end,
        }
        if self.extra:
            result["extra"] = self.extra
        if include_raw:
            result["raw"] = self.raw
        return result


@dataclass(frozen=True)
class UnparsedRecord:
    """A source block that cannot be safely interpreted as a log record."""

    line_start: int
    line_end: int
    raw: str

    def as_dict(self, *, include_raw: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "type": "unparsed",
            "line_start": self.line_start,
            "line_end": self.line_end,
        }
        if include_raw:
            result["raw"] = self.raw
        return result


def parse_log_line(line: str) -> ParsedLogLine | None:
    """Parse one supported timestamped log line, returning ``None`` if unmatched."""

    line = _strip_line_ending(line)
    glog = _GLOG_PREFIX.match(line)
    if glog is not None:
        return ParsedLogLine(
            timestamp=glog.group("timestamp"),
            level=_GLOG_SEVERITY[glog.group("severity")],
            process_id=None,
            service=None,
            thread_id=int(glog.group("thread_id")),
            source_file=glog.group("source_file"),
            source_line=int(glog.group("source_line")),
            message=glog.group("message"),
            raw=line,
        )

    match = _LOG_PREFIX.match(line)
    if match is None:
        return None

    rest = match.group("rest")
    process_id: int | None = None
    service: str | None = None
    thread_id: int | str | None = None
    source_file: str | None = None
    source_line: int | None = None
    extra: dict[str, Any] = {}

    context = _CONTEXT.match(rest)
    if context:
        context_text = context.group("context")
        pid_match = _PID.match(context_text)
        if pid_match:
            process_id = int(pid_match.group("process_id"))
            service = pid_match.group("service") or None
        else:
            extra["context"] = context_text
        tags = re.findall(r"\[([^\]]*)\]", context.group("tags"))
        if tags:
            extra["tags"] = tags
        rest = rest[context.end() :]

    thread = _THREAD.match(rest)
    if thread:
        thread_value = thread.group("thread_id")
        thread_id = int(thread_value) if thread_value.isdigit() else thread_value
        rest = rest[thread.end() :]

    source = _SOURCE.match(rest)
    if source:
        source_file = source.group("source_file")
        source_line = int(source.group("source_line"))
        rest = rest[source.end() :]

    return ParsedLogLine(
        timestamp=match.group("timestamp"),
        level=match.group("level").upper(),
        process_id=process_id,
        service=service,
        thread_id=thread_id,
        source_file=source_file,
        source_line=source_line,
        message=rest,
        raw=line,
        extra=extra,
    )


def iter_log_records(lines: Iterable[str]) -> Iterator[LogRecord | UnparsedRecord]:
    """Yield lossless records from an iterable of text lines.

    Ordinary non-record lines following a parsed record are continuations. Header
    lines and timestamp-looking lines that fail parsing become explicit unparsed
    blocks so malformed input is never silently discarded.
    """

    current: _RecordBuilder | None = None
    pending_unparsed: list[tuple[int, str]] = []

    for line_number, source_line in enumerate(lines, start=1):
        line = _strip_line_ending(source_line)
        parsed = parse_log_line(line)
        if parsed is not None:
            if current is not None:
                yield current.build()
            if pending_unparsed:
                yield _build_unparsed(pending_unparsed)
                pending_unparsed = []
            current = _RecordBuilder(parsed=parsed, line_start=line_number)
            continue

        if current is None:
            pending_unparsed.append((line_number, line))
        elif _is_explicit_unparsed(line):
            yield current.build()
            current = None
            pending_unparsed.append((line_number, line))
        else:
            current.continuation.append(line)

    if current is not None:
        yield current.build()
    if pending_unparsed:
        yield _build_unparsed(pending_unparsed)


def txt_to_json(
    content: str,
    *,
    output_format: Literal["jsonl", "array"] = "jsonl",
    include_raw: bool = True,
) -> str:
    """Convert text log content to JSONL or a JSON array."""

    _validate_output_format(output_format)
    records = iter_log_records(content.splitlines())
    if output_format == "array":
        return json.dumps(
            [record.as_dict(include_raw=include_raw) for record in records],
            ensure_ascii=False,
        )

    return "\n".join(
        json.dumps(record.as_dict(include_raw=include_raw), ensure_ascii=False)
        for record in records
    )


def convert_stream(
    source: TextIO,
    destination: TextIO,
    *,
    output_format: Literal["jsonl", "array"] = "jsonl",
    include_raw: bool = True,
) -> None:
    """Convert a text stream without materializing all output records."""

    _validate_output_format(output_format)
    records = iter_log_records(source)
    if output_format == "jsonl":
        for record in records:
            destination.write(json.dumps(record.as_dict(include_raw=include_raw), ensure_ascii=False))
            destination.write("\n")
        return

    destination.write("[")
    first = True
    for record in records:
        if not first:
            destination.write(",")
        destination.write(json.dumps(record.as_dict(include_raw=include_raw), ensure_ascii=False))
        first = False
    destination.write("]")


def _strip_line_ending(line: str) -> str:
    if line.endswith("\n"):
        line = line[:-1]
    if line.endswith("\r"):
        line = line[:-1]
    return line


def _is_explicit_unparsed(line: str) -> bool:
    stripped = line.lstrip()
    return stripped.startswith("#") or _TIMESTAMP_PREFIX.match(line) is not None


def _build_unparsed(lines: list[tuple[int, str]]) -> UnparsedRecord:
    return UnparsedRecord(
        line_start=lines[0][0],
        line_end=lines[-1][0],
        raw="\n".join(line for _, line in lines),
    )


@dataclass
class _RecordBuilder:
    parsed: ParsedLogLine
    line_start: int
    continuation: list[str] = field(default_factory=list)

    def build(self) -> LogRecord:
        raw_lines = [self.parsed.raw, *self.continuation]
        return LogRecord(
            timestamp=self.parsed.timestamp,
            level=self.parsed.level,
            process_id=self.parsed.process_id,
            service=self.parsed.service,
            thread_id=self.parsed.thread_id,
            source_file=self.parsed.source_file,
            source_line=self.parsed.source_line,
            message=self.parsed.message,
            line_start=self.line_start,
            line_end=self.line_start + len(raw_lines) - 1,
            continuation=tuple(self.continuation),
            raw="\n".join(raw_lines),
            extra=self.parsed.extra,
        )


def _validate_output_format(output_format: str) -> None:
    if output_format not in {"jsonl", "array"}:
        raise ValueError("output_format must be 'jsonl' or 'array'")
