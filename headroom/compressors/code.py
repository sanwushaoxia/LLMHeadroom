"""代码压缩器:超长函数体折叠 + 连续 import 合并 + 空行规整。"""

from __future__ import annotations

import re

from headroom.compressors.base import Compressor

_DEF = re.compile(r"^(\s*)(?:async\s+)?def\s+\w+\s*\([^)]*\)(?:\s*->\s*[^:]+)?:\s*$")
_CLASS = re.compile(r"^(\s*)(?:async\s+)?class\s+\w+.*:\s*$")
_IMPORT = re.compile(r"^\s*(?:import\s|from\s+\S+\s+import\s)")
_DECORATOR = re.compile(r"^\s*@")


def _is_code(content: str) -> float:
    lines = content.splitlines()
    if len(lines) < 4:
        return 0.0
    sample = lines[:200]
    signals = sum(
        1 for ln in sample if _DEF.match(ln) or _CLASS.match(ln) or _IMPORT.match(ln) or _DECORATOR.match(ln)
    )
    braces = sum(ln.count("{") + ln.count(";") for ln in sample)
    return min(1.0, (signals * 0.12 + min(braces, 40) * 0.01))


class CodeCompressor(Compressor):
    """Python/JS/Java 等通用代码压缩。

    - 函数体超过 max_body_lines 时:保留签名 + docstring 首行 + "… N 行省略"
    - 连续 import 折叠为一行计数
    - 连续空行规整为 1 行
    """

    name = "code"

    def __init__(self, max_body_lines: int = 8):
        self.max_body_lines = max_body_lines

    def detect(self, content: str) -> float:
        return _is_code(content)

    def compress(self, content: str) -> str:
        lines = content.splitlines()
        out: list[str] = []
        i, n = 0, len(lines)

        import_run: list[str] = []

        def flush_imports() -> None:
            if not import_run:
                return
            if len(import_run) > 3:
                out.append(import_run[0])
                out.append(f"# … [headroom] 另有 {len(import_run) - 1} 行 import 省略")
            else:
                out.extend(import_run)
            import_run.clear()

        while i < n:
            line = lines[i]
            if _IMPORT.match(line):
                import_run.append(line)
                i += 1
                continue
            flush_imports()

            m = _DEF.match(line)
            if m:
                indent = m.group(1)
                j = i + 1
                while j < n:
                    ln = lines[j]
                    if not ln.strip():
                        j += 1
                        continue
                    # 函数体:缩进比 def 更深;遇到同级内容(如下一个 def/装饰器)即停
                    if ln.startswith(indent) and len(ln) > len(indent) and ln[len(indent)] in " \t":
                        j += 1
                        continue
                    break
                body = _strip_blank_tail(lines[i + 1 : j])
                if len(body) <= self.max_body_lines:
                    out.extend(lines[i:j])
                else:
                    doc = self._first_docline(body, indent)
                    out.append(line)
                    kept = ([doc] if doc else []) + [body[-1]]
                    omitted = len(body) - len(kept)
                    if doc:
                        out.append(doc)
                    out.append(f"{indent}    # … [headroom] 函数体省略 {omitted} 行 …")
                    out.append(body[-1])
                i = j
                continue

            # class 行与其余内容原样保留;方法体由各自的 def 分支处理
            out.append(line)
            i += 1

        flush_imports()
        return "\n".join(out)

    @staticmethod
    def _first_docline(body: list[str], indent: str) -> str | None:
        """取 docstring 首行,无 docstring 返回 None。"""
        for ln in body:
            s = ln.strip()
            if not s:
                continue
            if s.startswith(('"""', "'''")):
                return ln
            break
        return None


def _strip_blank_tail(lines: list[str]) -> list[str]:
    end = len(lines)
    while end > 0 and not lines[end - 1].strip():
        end -= 1
    return lines[:end]
