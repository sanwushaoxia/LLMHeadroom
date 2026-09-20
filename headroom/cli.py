"""headroom 命令行入口。

子命令:
  headroom compress <file|->   压缩文件或 stdin
  headroom retrieve <ccr_id>   取回原文
  headroom stats               查看统计
  headroom mcp                 启动 MCP server(stdio)
"""

from __future__ import annotations

import argparse
import sys

from headroom import __version__
from headroom.core.pipeline import Headroom


def _read_source(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def cmd_compress(args: argparse.Namespace) -> int:
    content = _read_source(args.source)
    hr = Headroom()
    try:
        result = hr.compress(content)
        print(result.text)
        s = result.stats
        print(
            f"\n[headroom] compressor={s.compressor} "
            f"{s.original_bytes}B→{s.compressed_bytes}B saved={int(s.saved_ratio * 100)}%",
            file=sys.stderr,
        )
        return 0
    finally:
        hr.close()


def cmd_retrieve(args: argparse.Namespace) -> int:
    hr = Headroom()
    try:
        content = hr.retrieve(args.ccr_id, mode=args.mode)
        if content is None:
            print(f"[headroom] 未找到 ccr_id={args.ccr_id}(可能已过期)", file=sys.stderr)
            return 1
        print(content)
        return 0
    finally:
        hr.close()


def cmd_stats(_args: argparse.Namespace) -> int:
    hr = Headroom()
    try:
        for k, v in hr.stats().items():
            print(f"{k}: {v}")
        return 0
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

    p = sub.add_parser("compress", help="压缩文件或 stdin(-)")
    p.add_argument("source", help="文件路径,或 - 表示 stdin")
    p.set_defaults(func=cmd_compress)

    p = sub.add_parser("retrieve", help="按 ccr_id 取回原文")
    p.add_argument("ccr_id", help="8 位 CCR id")
    p.add_argument("--mode", choices=["full", "preview"], default="full")
    p.set_defaults(func=cmd_retrieve)

    p = sub.add_parser("stats", help="压缩统计")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("mcp", help="启动 MCP server(stdio)")
    p.set_defaults(func=cmd_mcp)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
