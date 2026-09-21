"""Headroom 错误类型。

错误码是跨 Python API、CLI 和 MCP 的稳定契约；调用方不需要解析中文提示。
"""

from __future__ import annotations


class HeadroomError(Exception):
    """所有可预期的 Headroom 错误基类。"""

    code = "HEADROOM_ERROR"

    def __init__(self, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "details": self.details}


class ConfigError(HeadroomError):
    code = "CONFIG_ERROR"


class InputDecodeError(HeadroomError):
    code = "INPUT_DECODE_ERROR"

    def __init__(self, source: str, encoding: str, error: UnicodeDecodeError):
        super().__init__(
            f"cannot decode {source!r} as {encoding}: byte {error.start} ({error.reason})",
            details={
                "source": source,
                "encoding": encoding,
                "position": error.start,
                "reason": error.reason,
            },
        )


class ContentTooLargeError(HeadroomError):
    code = "CONTENT_TOO_LARGE"

    def __init__(self, actual_bytes: int, max_bytes: int):
        super().__init__(
            f"content is {actual_bytes} UTF-8 bytes, maximum is {max_bytes}",
            details={"actual_bytes": actual_bytes, "max_bytes": max_bytes},
        )
        self.actual_bytes = actual_bytes
        self.max_bytes = max_bytes


class UnknownHintError(HeadroomError):
    code = "UNKNOWN_HINT"

    def __init__(self, hint: str, available: list[str]):
        super().__init__(
            f"unknown compressor hint: {hint!r}",
            details={"hint": hint, "available": available},
        )
        self.hint = hint
        self.available = available


class InvalidRetrieveModeError(HeadroomError):
    code = "INVALID_MODE"


class StoreCapacityError(HeadroomError):
    code = "STORE_CAPACITY_EXCEEDED"


class CCRNotFoundError(HeadroomError):
    code = "CCR_NOT_FOUND"


class CCRIntegrityError(HeadroomError):
    code = "CCR_INTEGRITY_ERROR"


class InvalidCompressorError(HeadroomError):
    code = "INVALID_COMPRESSOR"
