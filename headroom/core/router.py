"""ContentRouter:按置信度自动选择压缩器。"""

from __future__ import annotations

from headroom.compressors.base import Compressor
from headroom.compressors.code import CodeCompressor
from headroom.compressors.json import JsonCompressor
from headroom.compressors.log import LogCompressor
from headroom.compressors.text import TextCompressor


class ContentRouter:
    """注册若干压缩器,compress 时取 detect() 置信度最高者。

    置信度都不足 detect_threshold(默认 0.5)时落入 text 兜底压缩器。
    通过 router.register(...) 可扩展自定义压缩器。
    """

    def __init__(self, detect_threshold: float = 0.5, compressors: list[Compressor] | None = None):
        self.detect_threshold = detect_threshold
        self._compressors: list[Compressor] = compressors if compressors is not None else [
            LogCompressor(),
            JsonCompressor(),
            CodeCompressor(),
            TextCompressor(),
        ]

    def register(self, compressor: Compressor) -> None:
        """注册自定义压缩器,插入到兜底压缩器之前。"""
        text_idx = next((i for i, c in enumerate(self._compressors) if c.name == "text"), len(self._compressors))
        self._compressors.insert(text_idx, compressor)

    def select(self, content: str) -> Compressor:
        best: Compressor | None = None
        best_score = 0.0
        for c in self._compressors:
            score = c.detect(content)
            if score > best_score:
                best, best_score = c, score
        if best is None or best_score < self.detect_threshold:
            return self._fallback()
        return best

    def _fallback(self) -> Compressor:
        for c in self._compressors:
            if c.name == "text":
                return c
        return self._compressors[-1]

    @property
    def compressor_names(self) -> list[str]:
        return [c.name for c in self._compressors]
