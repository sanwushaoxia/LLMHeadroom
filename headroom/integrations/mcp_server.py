"""Headroom MCP Server(stdio)。

工具返回 JSON envelope，兼容旧客户端的 `text` 字段，同时提供稳定的错误码、分页和统计字段。
"""

from __future__ import annotations

import json

try:  # mcp 2.x
    from mcp.server.mcpserver import MCPServer
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as MCPServer  # type: ignore[no-redef]

from headroom.core.errors import CCRNotFoundError, HeadroomError, InvalidRetrieveModeError
from headroom.core.pipeline import Headroom

mcp = MCPServer(
    "headroom",
    instructions=(
        "Headroom 上下文压缩层。headroom_compress 返回 JSON envelope，"
        "结果中的 text 首行可能带 [headroom:ccr://<id>] 标记；"
        "大原文使用 headroom_retrieve 的 offset/limit 分页取回。"
    ),
)

_engine: Headroom | None = None


def _get_engine() -> Headroom:
    global _engine
    if _engine is None:
        _engine = Headroom()
    return _engine


def _ok(payload: dict) -> str:
    return json.dumps({"schema_version": 1, "ok": True, **payload}, ensure_ascii=False)


def _error(exc: Exception) -> str:
    if isinstance(exc, HeadroomError):
        error = exc.as_dict()
    else:
        error = {"code": "INTERNAL_ERROR", "message": str(exc), "details": {}}
    return json.dumps(
        {"schema_version": 1, "ok": False, "error": error}, ensure_ascii=False
    )


@mcp.tool()
def headroom_compress(content: str, hint: str | None = None) -> str:
    """压缩长内容并返回 JSON envelope。"""
    try:
        result = _get_engine().compress(content, hint=hint)
        return _ok({"text": result.text, **result.stats.as_dict()})
    except Exception as exc:
        return _error(exc)


@mcp.tool()
def headroom_retrieve(
    ccr_id: str,
    mode: str = "chunk",
    offset: int = 0,
    limit: int | None = None,
) -> str:
    """分页取回 CCR 原文；mode 可为 full、preview 或 chunk。"""
    try:
        if mode not in ("full", "preview", "chunk"):
            raise InvalidRetrieveModeError("mode must be 'full', 'preview' or 'chunk'")
        engine = _get_engine()
        if mode == "full":
            content = engine.retrieve(ccr_id, mode="full")
            if content is None:
                return _error(CCRNotFoundError("CCR entry not found", details={"ccr_id": ccr_id}))
            if limit is not None or offset != 0 or len(content) > engine.config.retrieve_default_limit:
                page = engine.retrieve_page(ccr_id, offset=offset, limit=limit)
                return _ok(page.as_dict()) if page else _error(
                    CCRNotFoundError("CCR entry not found", details={"ccr_id": ccr_id})
                )
            return _ok(
                {
                    "ccr_id": ccr_id,
                    "content": content,
                    "offset": 0,
                    "limit": len(content),
                    "total_chars": len(content),
                    "total_bytes": len(content.encode("utf-8")),
                    "next_offset": None,
                    "has_more": False,
                }
            )
        page_limit = engine.config.retrieve_default_limit if limit is None else limit
        page = engine.retrieve_page(ccr_id, offset=offset, limit=page_limit)
        if page is None:
            return _error(CCRNotFoundError("CCR entry not found", details={"ccr_id": ccr_id}))
        return _ok(page.as_dict())
    except Exception as exc:
        return _error(exc)


@mcp.tool()
def headroom_stats() -> str:
    """查看持久化压缩统计和当前 active CCR 库存。"""
    try:
        return _ok(_get_engine().stats())
    except Exception as exc:
        return _error(exc)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
