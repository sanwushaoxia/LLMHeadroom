"""Python 代码压缩器:函数体折叠、安全 import 处理和 AST 合法输出。"""

from __future__ import annotations

import ast
import re

from headroom.compressors.base import CompressionOutput, Compressor, Omission

_DEF_LINE = re.compile(r"^\s*(?:async\s+)?def\s+\w+")
_IMPORT = re.compile(r"^\s*(?:import\s|from\s+\S+\s+import\s)")


def _parse_python(content: str) -> ast.Module | None:
    try:
        return ast.parse(content)
    except SyntaxError:
        return None


class CodeCompressor(Compressor):
    """首版只处理 Python；非 Python 代码不会被伪装成 Python 输出。"""

    name = "code"

    def __init__(self, max_body_lines: int = 8):
        if max_body_lines <= 0:
            raise ValueError("max_body_lines must be greater than zero")
        self.max_body_lines = max_body_lines

    def detect(self, content: str) -> float:
        tree = _parse_python(content)
        if tree is None or not content.strip():
            return 0.0
        signals = sum(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom))
            for node in ast.walk(tree)
        )
        return min(1.0, 0.35 + signals * 0.08) if signals else 0.0

    def compress_with_metadata(self, content: str) -> CompressionOutput:
        tree = _parse_python(content)
        if tree is None:
            return CompressionOutput(text=content, language=None)
        lines = content.splitlines()
        functions: dict[int, ast.FunctionDef | ast.AsyncFunctionDef] = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and hasattr(node, "end_lineno"):
                functions[node.lineno - 1] = node

        out: list[str] = []
        omissions: list[Omission] = []
        i = 0
        import_run: list[str] = []

        def flush_imports() -> None:
            if not import_run:
                return
            if len(import_run) > 3:
                out.append(import_run[0])
                out.append(f"# … [headroom] 另有 {len(import_run) - 1} 行 import 省略")
                omissions.append(
                    Omission(
                        kind="imports",
                        reason="collapsed consecutive Python imports",
                        start=i - len(import_run),
                        end=i,
                        count=len(import_run) - 1,
                    )
                )
            else:
                out.extend(import_run)
            import_run.clear()

        while i < len(lines):
            line = lines[i]
            # Parenthesized/multiline imports are preserved as a unit.
            if _IMPORT.match(line) and "(" not in line:
                import_run.append(line)
                i += 1
                continue
            flush_imports()

            node = functions.get(i)
            if node is None:
                out.append(line)
                i += 1
                continue

            end = int(node.end_lineno or node.lineno)  # inclusive, 1-based
            body_start = self._body_start(node, lines, i, end)
            body_lines = lines[body_start:end]
            if len([line for line in body_lines if line.strip()]) <= self.max_body_lines:
                out.extend(lines[i:end])
                i = end
                continue

            header = lines[i:body_start]
            out.extend(header)
            doc_end = self._doc_end(node)
            if doc_end is not None and body_start < doc_end <= end:
                out.extend(lines[body_start:doc_end])
            indent = self._body_indent(lines[body_start:end], lines[i])
            omitted = max(1, len(body_lines) - max(1, (doc_end or body_start) - body_start))
            out.append(f"{indent}# … [headroom] 函数体省略 {omitted} 行 …")
            out.append(f"{indent}pass")
            omissions.append(
                Omission(
                    kind="function_body",
                    reason="folded long Python function body",
                    start=i,
                    end=end,
                    count=omitted,
                    details={"function": getattr(node, "name", "<anonymous>")},
                )
            )
            i = end

        flush_imports()
        return CompressionOutput(
            text="\n".join(out),
            lossy=bool(omissions),
            omissions=tuple(omissions),
            language="python",
        )

    def compress(self, content: str) -> str:
        return self.compress_with_metadata(content).text

    @staticmethod
    def _body_start(
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        lines: list[str],
        start: int,
        end: int,
    ) -> int:
        if node.body:
            first = node.body[0]
            return max(start + 1, first.lineno - 1)
        return min(end, start + 1)

    @staticmethod
    def _doc_end(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int | None:
        if node.body and isinstance(node.body[0], ast.Expr):
            value = node.body[0].value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                return int(node.body[0].end_lineno or node.body[0].lineno)
        return None

    @staticmethod
    def _body_indent(body: list[str], fallback: str) -> str:
        for line in body:
            if line.strip():
                return line[: len(line) - len(line.lstrip())]
        return fallback[: len(fallback) - len(fallback.lstrip())] + "    "
