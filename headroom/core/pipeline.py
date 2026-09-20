"""Headroom 门面:compress / retrieve / stats 一站式入口。"""

from __future__ import annotations

from headroom.ccr.marker import build_marker, embed
from headroom.ccr.store import SQLiteStore
from headroom.core.models import CompressResult, Config, Stats, estimate_tokens
from headroom.core.router import ContentRouter


class Headroom:
    """对外主入口。

    用法:
        hr = Headroom()
        result = hr.compress(big_text)
        print(result.text)          # 压缩结果(可能带 CCR 标记)
        hr.retrieve(ccr_id)         # LLM 按需取回原文
    """

    def __init__(self, config: Config | None = None, router: ContentRouter | None = None):
        self.config = config or Config()
        self.router = router or ContentRouter()
        self.store = SQLiteStore(self.config.db_path)
        self.total_original_tokens = 0
        self.total_compressed_tokens = 0
        self.total_calls = 0

    def compress(self, content: str, hint: str | None = None) -> CompressResult:
        """压缩内容;节省达到 min_ratio 时存入 CCR 并嵌入取回标记。"""
        if len(content) > self.config.max_content:
            content = content[: self.config.max_content]
        if hint:
            # hint 仅作提示信息,不参与路由决策;保留参数为未来扩展
            pass

        compressor = self.router.select(content)
        compressed = compressor.compress(content).strip("\n")

        orig_tokens = estimate_tokens(content)
        comp_tokens = estimate_tokens(compressed)
        saved_pct = int((1 - len(compressed) / max(1, len(content))) * 100)

        self.total_calls += 1
        self.total_original_tokens += orig_tokens
        self.total_compressed_tokens += comp_tokens

        if saved_pct < self.config.min_ratio * 100 or len(compressed) >= len(content):
            # 节省不足:原样返回,不引入 CCR 开销
            stats = Stats(
                original_bytes=len(content),
                compressed_bytes=len(content),
                original_tokens=orig_tokens,
                compressed_tokens=orig_tokens,
                compressor=compressor.name,
            )
            return CompressResult(text=content, stats=stats, retrievable=False)

        ccr_id = self.store.put(content, compressor=compressor.name)
        marker = build_marker(ccr_id, len(content), saved_pct)
        stats = Stats(
            original_bytes=len(content),
            compressed_bytes=len(compressed),
            original_tokens=orig_tokens,
            compressed_tokens=comp_tokens,
            compressor=compressor.name,
            ccr_id=ccr_id,
        )
        return CompressResult(text=embed(marker, compressed), stats=stats, retrievable=True)

    def retrieve(self, ccr_id: str, mode: str = "full", max_chars: int = 20000) -> str | None:
        """按 ccr_id 取回原文;mode="preview" 时只返回前 max_chars 字符。"""
        content = self.store.get(ccr_id)
        if content is None:
            return None
        if mode == "preview":
            return content[:max_chars]
        return content

    def stats(self) -> dict:
        return {
            "calls": self.total_calls,
            "original_tokens": self.total_original_tokens,
            "compressed_tokens": self.total_compressed_tokens,
            "saved_ratio": round(
                1 - self.total_compressed_tokens / max(1, self.total_original_tokens), 4
            ),
            "ccr_entries": self.store.entry_count(),
            "ccr_original_bytes": self.store.total_orig_bytes(),
            "db_path": str(self.config.db_path),
        }

    def close(self) -> None:
        self.store.close()
