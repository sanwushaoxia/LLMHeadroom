"""Headroom 门面:压缩、CCR 取回、分页和持久统计。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from headroom.ccr.marker import build_marker, embed
from headroom.ccr.store import SQLiteStore
from headroom.core.errors import ContentTooLargeError, InvalidRetrieveModeError
from headroom.core.models import CompressResult, Config, Stats, estimate_tokens, utf8_bytes
from headroom.core.router import ContentRouter
from headroom.compressors.base import CompressionOutput


@dataclass(frozen=True)
class RetrievePage:
    ccr_id: str
    content: str
    offset: int
    limit: int
    total_chars: int
    total_bytes: int
    next_offset: int | None
    has_more: bool
    content_hash: str

    def as_dict(self) -> dict:
        return {
            "ccr_id": self.ccr_id,
            "content": self.content,
            "offset": self.offset,
            "limit": self.limit,
            "total_chars": self.total_chars,
            "total_bytes": self.total_bytes,
            "next_offset": self.next_offset,
            "has_more": self.has_more,
            "content_hash": self.content_hash,
        }


class Headroom:
    """对外主入口。"""

    def __init__(self, config: Config | None = None, router: ContentRouter | None = None):
        self.config = config or Config()
        self.router = router or ContentRouter()
        self.store = SQLiteStore(
            self.config.db_path,
            ttl_seconds=self.config.ttl_hours * 3600,
            max_store_bytes=self.config.max_store_bytes,
            max_store_entries=self.config.max_store_entries,
        )

    def compress(self, content: str, hint: str | None = None) -> CompressResult:
        """压缩内容；输入超过 UTF-8 字节上限时明确失败，不静默截断。"""
        original_bytes = utf8_bytes(content)
        if original_bytes > self.config.max_content:
            raise ContentTooLargeError(original_bytes, self.config.max_content)

        compressor = self.router.select(content, hint=hint)
        report = compressor.compress_with_metadata(content)
        compressed = report.text.strip("\n")
        compressed_bytes = utf8_bytes(compressed)
        original_tokens = estimate_tokens(content)
        compressed_tokens = estimate_tokens(compressed)

        # 先根据正文判断是否值得存 CCR；marker 的开销也计入最终 emitted 统计。
        body_ratio = 0.0 if original_bytes == 0 else 1 - compressed_bytes / original_bytes
        ccr_id: str | None = None
        output = content
        retrievable = False
        warnings: list[str] = list(report.warnings)
        marker_size = 0
        if body_ratio >= self.config.min_ratio and compressed_bytes < original_bytes:
            # UUID 是固定 32 个 hex 字符，placeholder 与实际 marker 长度一致。
            candidate_id = "0" * 32
            candidate_marker = build_marker(candidate_id, original_bytes, round(body_ratio * 100))
            marker_size = utf8_bytes(candidate_marker)
            if marker_size + compressed_bytes < original_bytes:
                candidate_output = embed(candidate_marker, compressed)
                candidate_emitted_bytes = utf8_bytes(candidate_output)
                candidate_emitted_tokens = estimate_tokens(candidate_output)
                ccr_id = self.store.put_with_metrics(
                    content,
                    compressor.name,
                    original_bytes=original_bytes,
                    emitted_bytes=candidate_emitted_bytes,
                    original_tokens=original_tokens,
                    emitted_tokens=candidate_emitted_tokens,
                    ccr=True,
                )
                marker = build_marker(ccr_id, original_bytes, round(body_ratio * 100))
                output = embed(marker, compressed)
                retrievable = True
            else:
                warnings.append("CCR marker overhead would exceed original size; original returned")
        if not retrievable and compressed_bytes >= original_bytes:
            warnings.append("compressed body was not smaller; original content returned")

        emitted_bytes = utf8_bytes(output)
        emitted_tokens = estimate_tokens(output)
        if not retrievable:
            self.store.record_metrics(
                original_bytes=original_bytes,
                emitted_bytes=emitted_bytes,
                original_tokens=original_tokens,
                emitted_tokens=emitted_tokens,
                ccr=False,
            )
        stats = Stats(
            original_bytes=original_bytes,
            compressed_bytes=compressed_bytes if retrievable else original_bytes,
            emitted_bytes=emitted_bytes,
            original_tokens=original_tokens,
            compressed_tokens=compressed_tokens if retrievable else original_tokens,
            emitted_tokens=emitted_tokens,
            compressor=compressor.name,
            ccr_id=ccr_id,
            retrievable=retrievable,
            hint=hint.strip().lower() if hint and hint.strip() else None,
            warnings=warnings,
            lossy=report.lossy,
            omissions=[item.as_dict() for item in report.omissions],
        )
        return CompressResult(text=output, stats=stats, retrievable=retrievable, warnings=warnings)

    def retrieve(
        self,
        ccr_id: str,
        mode: str = "full",
        max_chars: int | None = None,
    ) -> str | None:
        """兼容旧 API 的取回包装；大内容建议使用 retrieve_page。"""
        if mode not in ("full", "preview"):
            raise InvalidRetrieveModeError("mode must be 'full' or 'preview'")
        content = self.store.get(ccr_id)
        if content is None:
            return None
        if mode == "preview":
            limit = max_chars if max_chars is not None else self.config.retrieve_default_limit
            if limit < 0:
                raise InvalidRetrieveModeError("max_chars must be non-negative")
            return content[:limit]
        return content

    def retrieve_page(
        self,
        ccr_id: str,
        *,
        offset: int = 0,
        limit: int | None = None,
    ) -> RetrievePage | None:
        if offset < 0:
            raise InvalidRetrieveModeError("offset must be non-negative")
        page_limit = limit if limit is not None else self.config.retrieve_default_limit
        if page_limit <= 0:
            raise InvalidRetrieveModeError("limit must be greater than zero")
        content = self.store.get(ccr_id)
        if content is None:
            return None
        end = min(len(content), offset + page_limit)
        page = content[offset:end]
        next_offset = end if end < len(content) else None
        return RetrievePage(
            ccr_id=ccr_id,
            content=page,
            offset=offset,
            limit=page_limit,
            total_chars=len(content),
            total_bytes=utf8_bytes(content),
            next_offset=next_offset,
            has_more=next_offset is not None,
            content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        )

    def stats(self) -> dict:
        metrics = self.store.metrics()
        original = int(metrics["original_tokens"])
        emitted = int(metrics["emitted_tokens"])
        return {
            "calls": int(metrics["calls"]),
            "ccr_calls": int(metrics["ccr_calls"]),
            "original_bytes": int(metrics["original_bytes"]),
            "emitted_bytes": int(metrics["emitted_bytes"]),
            "original_tokens": original,
            "compressed_tokens": emitted,
            "emitted_tokens": emitted,
            "saved_ratio": 0.0 if original == 0 else max(0.0, min(1.0, 1 - emitted / original)),
            "ccr_entries": self.store.entry_count(),
            "ccr_original_bytes": self.store.total_orig_bytes(),
            "ccr_stored_bytes": self.store.total_stored_bytes(),
            "db_path": str(self.config.db_path),
            "scope": "database",
        }

    def close(self) -> None:
        self.store.close()
