"""headroom 命令行入口。"""

from __future__ import annotations

import argparse
import io
import json
import sys
from contextlib import contextmanager
from typing import Iterator, TextIO

from headroom import __version__
from headroom.cli_config import CliDefaults, load_defaults
from headroom.core.errors import HeadroomError, InputDecodeError
from headroom.core.models import Config
from headroom.core.pipeline import Headroom
from headroom.log_conversion import convert_stream


def _read_source(path: str, encoding: str = "utf-8") -> str:
    if path == "-":
        stream = getattr(sys.stdin, "buffer", None)
        data = stream.read() if stream is not None else sys.stdin.read().encode(encoding)
        source = "<stdin>"
    else:
        with open(path, "rb") as file:
            data = file.read()
        source = path
    try:
        return data.decode(encoding)
    except UnicodeDecodeError as exc:
        raise InputDecodeError(source, encoding, exc) from exc


@contextmanager
def _open_convert_source(path: str, encoding: str) -> Iterator[TextIO]:
    source_name = "<stdin>" if path == "-" else path
    if path != "-":
        with open(path, "r", encoding=encoding, errors="strict", newline="") as source:
            try:
                yield source
            except UnicodeDecodeError as exc:
                raise InputDecodeError(source_name, encoding, exc) from exc
        return

    binary = getattr(sys.stdin, "buffer", None)
    if binary is None:
        yield io.StringIO(sys.stdin.read())
        return
    wrapper = io.TextIOWrapper(binary, encoding=encoding, errors="strict", newline="")
    try:
        try:
            yield wrapper
        except UnicodeDecodeError as exc:
            raise InputDecodeError(source_name, encoding, exc) from exc
    finally:
        wrapper.detach()


@contextmanager
def _open_convert_destination(path: str) -> Iterator[TextIO]:
    if path == "-":
        yield sys.stdout
        return
    with open(path, "w", encoding="utf-8", newline="") as destination:
        yield destination


def _error(exc: Exception) -> int:
    if isinstance(exc, HeadroomError):
        payload = exc.as_dict()
    else:
        payload = {"code": "INTERNAL_ERROR", "message": str(exc), "details": {}}
    print(json.dumps({"ok": False, "error": payload}, ensure_ascii=False), file=sys.stderr)
    return 2


def _effective_compress_options(args: argparse.Namespace, defaults: CliDefaults) -> tuple[str, str | None, int | None]:
    encoding = args.encoding if args.encoding is not None else (defaults.encoding or "utf-8")
    hint = args.hint if args.hint is not None else defaults.hint
    if args.max_content is not None:
        max_content = args.max_content
    elif "HEADROOM_MAX_CONTENT" in __import__("os").environ:
        max_content = None  # Config() must preserve the existing env precedence.
    else:
        max_content = defaults.max_content
    return encoding, hint, max_content


def cmd_compress(args: argparse.Namespace) -> int:
    hr: Headroom | None = None
    try:
        defaults = load_defaults(args.config)
        encoding, hint, max_content = _effective_compress_options(args, defaults)
        config = Config(max_content=max_content) if max_content is not None else None
        hr = Headroom(config=config)
        result = hr.compress(_read_source(args.source, encoding), hint=hint)
        if args.json:
            print(json.dumps({"ok": True, "text": result.text, **result.stats.as_dict()}, ensure_ascii=False))
        else:
            print(result.text)
            s = result.stats
            print(
                f"\n[headroom] compressor={s.compressor} "
                f"{s.original_bytes}B→{s.emitted_bytes}B saved={int(s.saved_ratio * 100)}%",
                file=sys.stderr,
            )
        return 0
    except Exception as exc:
        return _error(exc)
    finally:
        if hr is not None:
            hr.close()


def cmd_convert(args: argparse.Namespace) -> int:
    try:
        defaults = load_defaults(args.config)
        encoding = args.encoding if args.encoding is not None else (defaults.encoding or "utf-8")
        with _open_convert_source(args.source, encoding) as source:
            with _open_convert_destination(args.output) as destination:
                convert_stream(
                    source,
                    destination,
                    output_format=args.format,
                    include_raw=not args.no_raw,
                )
        return 0
    except Exception as exc:
        return _error(exc)


def cmd_retrieve(args: argparse.Namespace) -> int:
    hr = Headroom()
    try:
        page = hr.retrieve_page(args.ccr_id, offset=args.offset, limit=args.limit)
        if page is None:
            print(
                json.dumps(
                    {"ok": False, "error": {"code": "CCR_NOT_FOUND", "message": "CCR entry not found"}},
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
            return 1
        if args.json:
            print(json.dumps({"ok": True, **page.as_dict()}, ensure_ascii=False))
        else:
            print(page.content, end="" if page.content.endswith("\n") else "\n")
            if page.has_more:
                print(f"[headroom] next_offset={page.next_offset}", file=sys.stderr)
        return 0
    except Exception as exc:
        return _error(exc)
    finally:
        hr.close()


def cmd_stats(args: argparse.Namespace) -> int:
    hr = Headroom()
    try:
        stats = hr.stats()
        if args.json:
            print(json.dumps({"ok": True, **stats}, ensure_ascii=False))
        else:
            for key, value in stats.items():
                print(f"{key}: {value}")
        return 0
    except Exception as exc:
        return _error(exc)
    finally:
        hr.close()


def cmd_mcp(_args: argparse.Namespace) -> int:
    from headroom.integrations.mcp_server import main as mcp_main

    mcp_main()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="headroom",
        description="Headroom — 通用上下文压缩层(压缩器自动路由 + CCR 按需取回)",
    )
    parser.add_argument("--version", action="version", version=f"headroom {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    compress = sub.add_parser("compress", help="压缩文件或 stdin(-)")
    compress.add_argument("source", help="文件路径,或 - 表示 stdin")
    compress.add_argument("--hint", choices=["log", "json", "code", "text"], default=None)
    compress.add_argument("--encoding", default=None, help="输入文件编码，默认读 CLI 配置文件，缺省 utf-8")
    compress.add_argument("--max-content", type=int, default=None, help="本次输入最大 UTF-8 字节数")
    compress.add_argument("--config", default=None, help="CLI 配置文件路径，默认读 ~/.headroom/config.json")
    compress.add_argument("--json", action="store_true", help="输出 JSON envelope")
    compress.set_defaults(func=cmd_compress)

    convert = sub.add_parser("convert", help="将纯文本日志转换为 JSON 或 JSONL")
    convert.add_argument("source", help="文件路径,或 - 表示 stdin")
    convert.add_argument("--from", dest="source_format", choices=["txt"], default="txt")
    convert.add_argument("--to", dest="target_format", choices=["json"], default="json")
    convert.add_argument("--format", choices=["jsonl", "array"], default="jsonl")
    convert.add_argument("--encoding", default=None, help="输入文件编码，默认读 CLI 配置文件，缺省 utf-8")
    convert.add_argument("--config", default=None, help="CLI 配置文件路径，默认读 ~/.headroom/config.json")
    convert.add_argument("--output", default="-", help="输出文件路径，默认 stdout(-)")
    convert.add_argument("--no-raw", action="store_true", help="省略每条记录的原始文本")
    convert.set_defaults(func=cmd_convert)

    retrieve = sub.add_parser("retrieve", help="分页取回 CCR 原文")
    retrieve.add_argument("ccr_id", help="CCR id(支持完整 ID 或旧 8 位前缀)")
    retrieve.add_argument("--offset", type=int, default=0)
    retrieve.add_argument("--limit", type=int, default=None)
    retrieve.add_argument("--json", action="store_true", help="输出 JSON envelope")
    retrieve.set_defaults(func=cmd_retrieve)

    stats = sub.add_parser("stats", help="查看持久化压缩统计")
    stats.add_argument("--json", action="store_true", help="输出 JSON envelope")
    stats.set_defaults(func=cmd_stats)

    mcp = sub.add_parser("mcp", help="启动 MCP server(stdio)")
    mcp.set_defaults(func=cmd_mcp)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
