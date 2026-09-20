"""压缩器抽象基类。"""

from __future__ import annotations

from abc import ABC, abstractmethod


class Compressor(ABC):
    """专用压缩器:detect 给出置信度,router 择优;compress 返回压缩文本。"""

    name: str = "base"

    @abstractmethod
    def detect(self, content: str) -> float:
        """返回 0~1 置信度,表示该压缩器对此内容的适用程度。"""

    @abstractmethod
    def compress(self, content: str) -> str:
        """执行压缩,返回压缩后文本(不含 CCR 标记)。"""
