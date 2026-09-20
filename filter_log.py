#!/usr/bin/env python3
"""从日志文件中筛选包含指定字符串的行，写入新的日志文件。

用法:
    python3 filter_log.py <日志文件> <过滤字符串> [输出文件] [选项]

示例:
    python3 filter_log.py app.log ERROR
    python3 filter_log.py app.log ERROR error_only.log -i
"""

import argparse
import sys
from pathlib import Path


def filter_log(
    input_path: Path,
    keyword: str,
    output_path: Path,
    ignore_case: bool = False,
    invert: bool = False,
    encoding: str = "utf-8",
) -> int:
    """筛选日志行并写入输出文件，返回匹配的行数。"""
    count = 0
    with input_path.open("r", encoding=encoding, errors="replace") as fin, \
         output_path.open("w", encoding=encoding) as fout:
        for line in fin:
            haystack, needle = line, keyword
            if ignore_case:
                haystack, needle = haystack.lower(), needle.lower()
            if (needle in haystack) != invert:
                fout.write(line)
                count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser(
        description="筛选日志文件中包含指定字符串的行，生成新的日志文件。"
    )
    parser.add_argument("input", type=Path, help="输入日志文件路径")
    parser.add_argument("keyword", help="要筛选的字符串")
    parser.add_argument(
        "output", type=Path, nargs="?",
        help="输出文件路径，默认为 <输入文件名>.filtered.log",
    )
    parser.add_argument(
        "-i", "--ignore-case", action="store_true", help="忽略大小写"
    )
    parser.add_argument(
        "-v", "--invert", action="store_true",
        help="反向筛选：输出不包含该字符串的行",
    )
    parser.add_argument(
        "--encoding", default="utf-8", help="文件编码，默认 utf-8"
    )
    args = parser.parse_args()

    if not args.input.is_file():
        print(f"错误：输入文件不存在：{args.input}", file=sys.stderr)
        return 1

    output = args.output or args.input.with_suffix(".filtered.log")
    count = filter_log(
        args.input, args.keyword, output,
        ignore_case=args.ignore_case,
        invert=args.invert,
        encoding=args.encoding,
    )
    print(f"共筛选出 {count} 行，已写入 {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
