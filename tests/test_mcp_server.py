"""MCP server 工具测试:直接调用注册的函数与 MCPServer.call_tool。"""

from __future__ import annotations

import asyncio
import os

import pytest

from headroom.ccr.marker import extract_id
from headroom.integrations import mcp_server


@pytest.fixture()
def isolated_engine(tmp_path, monkeypatch):
    """让 MCP server 的引擎使用临时数据库。"""
    monkeypatch.setenv("HEADROOM_DB", str(tmp_path / "mcp.db"))
    mcp_server._engine = None  # 强制重建
    yield
    if mcp_server._engine is not None:
        mcp_server._engine.close()
    mcp_server._engine = None


BIG_LOG = "\n".join(
    f"2024-01-01 00:{i // 60:02d}:{i % 60:02d} DEBUG healthcheck ok node-{i % 5}" for i in range(300)
)


class TestMcpTools:
    def test_compress_returns_marker_and_stats(self, isolated_engine):
        out = mcp_server.headroom_compress(BIG_LOG)
        assert "[headroom:ccr://" in out
        assert "compressor=log" in out

    def test_compress_small_no_marker(self, isolated_engine):
        out = mcp_server.headroom_compress("tiny")
        assert "ccr://" not in out

    def test_retrieve_roundtrip(self, isolated_engine):
        out = mcp_server.headroom_compress(BIG_LOG)
        cid = extract_id(out)
        assert mcp_server.headroom_retrieve(cid) == BIG_LOG

    def test_retrieve_unknown_id(self, isolated_engine):
        out = mcp_server.headroom_retrieve("ffffffff")
        assert "未找到" in out

    def test_retrieve_bad_mode(self, isolated_engine):
        out = mcp_server.headroom_retrieve("ffffffff", mode="bogus")
        assert "无效 mode" in out

    def test_stats(self, isolated_engine):
        mcp_server.headroom_compress(BIG_LOG)
        out = mcp_server.headroom_stats()
        assert "calls=1" in out
        assert "ccr_entries=1" in out

    def test_via_call_tool_protocol(self, isolated_engine):
        """走 MCP 协议层调用,验证注册与返回结构。"""
        result = asyncio.run(mcp_server.mcp.call_tool("headroom_compress", {"content": BIG_LOG}))
        assert result.is_error is False
        text = result.content[0].text
        assert "[headroom:ccr://" in text

    def test_list_tools(self):
        tools = asyncio.run(mcp_server.mcp.list_tools())
        names = {t.name for t in tools}
        assert names == {"headroom_compress", "headroom_retrieve", "headroom_stats"}
