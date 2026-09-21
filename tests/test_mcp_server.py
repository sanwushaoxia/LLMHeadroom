"""MCP server 工具测试:结构化 JSON envelope、错误码和分页。"""

from __future__ import annotations

import asyncio
import json

import pytest

from headroom.ccr.marker import extract_id
from headroom.integrations import mcp_server


@pytest.fixture()
def isolated_engine(tmp_path, monkeypatch):
    monkeypatch.setenv("HEADROOM_DB", str(tmp_path / "mcp.db"))
    mcp_server._engine = None
    yield
    if mcp_server._engine is not None:
        mcp_server._engine.close()
    mcp_server._engine = None


BIG_LOG = "\n".join(
    f"2024-01-01 00:{i // 60:02d}:{i % 60:02d} DEBUG healthcheck ok node-{i % 5}" for i in range(300)
)


def parse_response(value: str) -> dict:
    return json.loads(value)


class TestMcpTools:
    def test_compress_returns_structured_response(self, isolated_engine):
        payload = parse_response(mcp_server.headroom_compress(BIG_LOG))
        assert payload["ok"] is True
        assert "[headroom:ccr://" in payload["text"]
        assert payload["compressor"] == "log"
        assert payload["retrievable"] is True
        assert payload["original_bytes"] == len(BIG_LOG.encode())
        assert payload["schema_version"] == 1

    def test_compress_small_no_marker(self, isolated_engine):
        payload = parse_response(mcp_server.headroom_compress("tiny"))
        assert payload["ok"] is True
        assert "ccr://" not in payload["text"]
        assert payload["retrievable"] is False

    def test_retrieve_roundtrip_and_pagination(self, isolated_engine):
        compressed = parse_response(mcp_server.headroom_compress(BIG_LOG))
        cid = extract_id(compressed["text"])
        assert cid
        first = parse_response(mcp_server.headroom_retrieve(cid, mode="chunk", limit=100))
        assert first["ok"] is True
        pieces = [first["content"]]
        next_offset = first["next_offset"]
        while next_offset is not None:
            page = parse_response(
                mcp_server.headroom_retrieve(cid, mode="chunk", offset=next_offset, limit=100)
            )
            assert page["ok"] is True
            pieces.append(page["content"])
            next_offset = page["next_offset"]
        assert "".join(pieces) == BIG_LOG
        assert first["total_bytes"] == len(BIG_LOG.encode())

    def test_retrieve_unknown_id(self, isolated_engine):
        payload = parse_response(mcp_server.headroom_retrieve("ffffffff"))
        assert payload["ok"] is False
        assert payload["error"]["code"] == "CCR_NOT_FOUND"

    def test_retrieve_bad_mode(self, isolated_engine):
        payload = parse_response(mcp_server.headroom_retrieve("ffffffff", mode="bogus"))
        assert payload["ok"] is False
        assert payload["error"]["code"] == "INVALID_MODE"

    def test_stats(self, isolated_engine):
        mcp_server.headroom_compress(BIG_LOG)
        payload = parse_response(mcp_server.headroom_stats())
        assert payload["ok"] is True
        assert payload["calls"] == 1
        assert payload["ccr_entries"] == 1
        assert payload["scope"] == "database"

    def test_via_call_tool_protocol(self, isolated_engine):
        result = asyncio.run(mcp_server.mcp.call_tool("headroom_compress", {"content": BIG_LOG}))
        assert result.is_error is False
        payload = parse_response(result.content[0].text)
        assert payload["ok"] is True
        assert "[headroom:ccr://" in payload["text"]

    def test_list_tools(self):
        tools = asyncio.run(mcp_server.mcp.list_tools())
        names = {tool.name for tool in tools}
        assert names == {"headroom_compress", "headroom_retrieve", "headroom_stats"}
