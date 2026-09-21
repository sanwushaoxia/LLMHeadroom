"""压缩器抽象接口和结构化 loss metadata。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Omission:
    """一次可机器读取的省略/变换记录。"""

    kind: str
    reason: str
    path: str | None = None
    start: int | None = None
    end: int | None = None
    count: int | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "reason": self.reason,
            "path": self.path,
            "start": self.start,
            "end": self.end,
            "count": self.count,
            "details": self.details,
        }


@dataclass
class CompressionOutput:
    """压缩正文及其损失说明。"""

    text: str
    lossy: bool = False
    omissions: tuple[Omission, ...] = ()
    language: str | None = None

    @property
    def warnings(self) -> list[str]:
        return [omission.reason for omission in self.omissions]

    def as_dict(self) -> dict[str, Any]:
        return {
            "lossy": self.lossy,
            "language": self.language,
            "omissions": [item.as_dict() for item in self.omissions],
        }


class Compressor(ABC):
    """专用压缩器:detect 给出置信度,compress 返回兼容文本。"""

    name: str = "base"

    @abstractmethod
    def detect(self, content: str) -> float:
        """返回 0~1 置信度。"""

    @abstractmethod
    def compress(self, content: str) -> str:
        """执行压缩,返回压缩后文本(不含 CCR 标记)。"""

    def compress_with_metadata(self, content: str) -> CompressionOutput:
        """默认兼容包装；专用压缩器可覆盖以提供结构化报告。"""
        return CompressionOutput(text=self.compress(content))
