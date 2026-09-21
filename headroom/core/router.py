"""ContentRouter:按 hint 或置信度自动选择压缩器。"""

from __future__ import annotations

import math

from headroom.compressors.base import Compressor
from headroom.compressors.code import CodeCompressor
from headroom.compressors.json import JsonCompressor
from headroom.compressors.log import LogCompressor
from headroom.compressors.text import TextCompressor
from headroom.core.errors import InvalidCompressorError, UnknownHintError


class ContentRouter:
    """注册压缩器，显式 hint 优先，否则取 detect() 置信度最高者。"""

    def __init__(self, detect_threshold: float = 0.5, compressors: list[Compressor] | None = None):
        if not 0 <= detect_threshold <= 1 or not math.isfinite(detect_threshold):
            raise InvalidCompressorError("detect_threshold must be between 0 and 1")
        self.detect_threshold = detect_threshold
        self._compressors: list[Compressor] = compressors if compressors is not None else [
            LogCompressor(),
            JsonCompressor(),
            CodeCompressor(),
            TextCompressor(),
        ]
        self._validate_registry()

    def _validate_registry(self) -> None:
        if not self._compressors:
            raise InvalidCompressorError("at least one compressor is required")
        names: set[str] = set()
        for compressor in self._compressors:
            name = str(getattr(compressor, "name", "")).strip().lower()
            if not name:
                raise InvalidCompressorError("compressor name must not be empty")
            if name in names:
                raise InvalidCompressorError(f"duplicate compressor name: {name}")
            names.add(name)
        if "text" not in names:
            raise InvalidCompressorError("a text fallback compressor is required")

    def register(self, compressor: Compressor) -> None:
        """注册自定义压缩器，插入到 text 兜底之前。"""
        name = str(getattr(compressor, "name", "")).strip().lower()
        if not name:
            raise InvalidCompressorError("compressor name must not be empty")
        if name in {c.name.lower() for c in self._compressors}:
            raise InvalidCompressorError(f"duplicate compressor name: {name}")
        text_idx = next(i for i, c in enumerate(self._compressors) if c.name.lower() == "text")
        self._compressors.insert(text_idx, compressor)

    def select(self, content: str, hint: str | None = None) -> Compressor:
        if hint is not None and hint.strip():
            normalized = hint.strip().lower()
            for compressor in self._compressors:
                if compressor.name.lower() == normalized:
                    return compressor
            raise UnknownHintError(normalized, self.compressor_names)

        best: Compressor | None = None
        best_score = 0.0
        for compressor in self._compressors:
            score = compressor.detect(content)
            if not isinstance(score, (int, float)) or not 0 <= score <= 1 or not math.isfinite(score):
                raise InvalidCompressorError(
                    f"compressor {compressor.name!r} returned invalid detect score: {score!r}"
                )
            if score > best_score:
                best, best_score = compressor, float(score)
        if best is None or best_score < self.detect_threshold:
            return self._fallback()
        return best

    def _fallback(self) -> Compressor:
        for compressor in self._compressors:
            if compressor.name.lower() == "text":
                return compressor
        raise InvalidCompressorError("a text fallback compressor is required")

    @property
    def compressor_names(self) -> list[str]:
        return [c.name for c in self._compressors]
