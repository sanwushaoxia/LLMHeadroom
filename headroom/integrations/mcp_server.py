"""Headroom MCP Server(stdio)。

启动:python -m headroom.mcp_server  或  headroom mcp
挂载:claude mcp add headroom -- python -m headroom.mcp_server

暴露 3 个工具:
- headroom_compress(content, hint?) — 压缩长内容,返回带 CCR 标记的结果
- headroom_retrieve(ccr_id, mode)   — 按需取回原文(full / preview)
- headroom_stats()                  — 压缩统计与库存情况
"""

from __future__ import annotations

try:  # mcp 2.x
    from mcp.server.mcpserver import MCPServer
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as MCPServer  # type: ignore[no-redef]

from headroom.core.pipeline import Headroom

mcp = MCPServer(
    "headroom",
    instructions=(
        "Headroom 上下文压缩层:用 headroom_compress 压缩长内容(日志/JSON/代码),"
        "结果首行附带 [headroom:ccr://<id>] 标记;需要完整原文时调用 headroom_retrieve(ccr_id)。"
    ),
)

_engine: Headroom | None = None


def _get_engine() -> Headroom:
    global _engine
    if _engine is None:
        _engine = Headroom()
    return _engine


@mcp.tool()
def headroom_compress(content: str, hint: str | None = None) -> str:
    """压缩长内容(日志/JSON/代码/文本),自动路由到专用压缩器。

    返回压缩后的文本;若节省显著,首行附带 CCR 标记,
    之后可用 headroom_retrieve(ccr_id) 随时取回完整原文。
    hint 为可选的内容类型提示(如 "log"/"json"/"code")。
    """
    result = _get_engine().compress(content, hint=hint)
    s = result.stats
    footer = (
        f"\n\n[headroom] compressor={s.compressor} "
        f"orig={s.original_bytes}B→{s.compressed_bytes}B "
        f"saved={int(s.saved_ratio * 100)}% "
        f"tokens≈{s.original_tokens}→{s.compressed_tokens}"
    )
    return result.text + footer


@mcp.tool()
def headroom_retrieve(ccr_id: str, mode: str = "full") -> str:
    """取回被压缩内容的完整原文。

    ccr_id 来自压缩结果首行的 [headroom:ccr://<id>] 标记。
    mode="full" 返回完整原文;mode="preview" 只返回前 20000 字符。
    """
    if mode not in ("full", "preview"):
        return f"[headroom] 无效 mode={mode!r},可选 full / preview"
    content = _get_engine().retrieve(ccr_id, mode=mode)
    if content is None:
        return (
            f"[headroom] 未找到 ccr_id={ccr_id} 的存档(可能已过期或 id 有误)。"
            "TTL 默认 72 小时,过期条目不可恢复。"
        )
    return content


@mcp.tool()
def headroom_stats() -> str:
    """查看 Headroom 压缩统计:调用次数、累计节省、CCR 库存。"""
    st = _get_engine().stats()
    return (
        "[headroom] stats:\n"
        f"  calls={st['calls']}  saved_ratio={st['saved_ratio']:.1%}\n"
        f"  original_tokens≈{st['original_tokens']}  compressed_tokens≈{st['compressed_tokens']}\n"
        f"  ccr_entries={st['ccr_entries']}  ccr_original_bytes={st['ccr_original_bytes']}\n"
        f"  db={st['db_path']}"
    )


def main() -> None:
    mcp.run(transport="stdio")
if __name__ == "__main__":
    main()
