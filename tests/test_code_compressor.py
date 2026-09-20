"""代码压缩器测试。"""

from headroom.compressors.code import CodeCompressor

PYTHON_CODE = '''#!/usr/bin/env python3
import os
import sys
import json
import logging
import threading
import asyncio

CONSTANT_A = 1
CONSTANT_B = 2


def tiny():
    return 1


def huge_function(a, b):
    """处理核心业务逻辑。"""
    total = 0
    for i in range(100):
        total += i * a
        if total > 1000:
            total -= b
        total = total % 9973
        logging.info("step %d", i)
    return total


def another_huge(x):
    """另一个长函数。"""
    rows = []
    for i in range(50):
        rows.append({"idx": i, "val": x * i})
        rows.append({"idx": i, "val": x + i})
    result = sorted(rows, key=lambda r: r["val"])
    cleaned = [r for r in result if r["val"] > 0]
    return cleaned[:10]


class Service:
    def method_small(self):
        return 2

    def method_huge(self):
        acc = []
        for i in range(200):
            acc.append(i)
            acc.append(i * 2)
            acc.append(i * 3)
        return acc
'''


def test_detect_python():
    assert CodeCompressor().detect(PYTHON_CODE) >= 0.5


def test_detect_rejects_prose():
    assert CodeCompressor().detect("普通段落文字\n" * 10) < 0.3


def test_long_function_body_folded():
    out = CodeCompressor(max_body_lines=6).compress(PYTHON_CODE)
    assert "函数体省略" in out
    assert "处理核心业务逻辑" in out  # docstring 首行保留
    assert "logging.info" not in out  # 函数体细节被省略


def test_short_function_untouched():
    out = CodeCompressor(max_body_lines=6).compress(PYTHON_CODE)
    assert "def tiny():" in out
    assert "return 1" in out


def test_imports_collapsed():
    out = CodeCompressor().compress(PYTHON_CODE)
    assert "另有" in out and "行 import 省略" in out
    assert "import threading" not in out


def test_output_still_parseable_python():
    import ast

    out = CodeCompressor(max_body_lines=6).compress(PYTHON_CODE)
    ast.parse(out)  # 折叠后仍是合法 Python(省略行是注释)
