"""端到端验收:真实 stdio 子进程 + 官方 MCP client 会话。

验证 initialize、tools/list、结构化 compress、分页 retrieve、stats 和完整拼接。
"""

import asyncio
import json
import os
import sys
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def run() -> None:
    tmp = tempfile.mkdtemp()
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "headroom.mcp_server"],
        env=dict(os.environ, HEADROOM_DB=os.path.join(tmp, "e2e.db")),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            assert names == {"headroom_compress", "headroom_retrieve", "headroom_stats"}, names
            print("tools:", sorted(names))

            big = "\n".join(
                f"2024-01-01 00:{i // 60:02d}:{i % 60:02d} DEBUG healthcheck ok node-{i % 5}"
                for i in range(300)
            )
            comp_result = await session.call_tool("headroom_compress", {"content": big})
            comp = json.loads(comp_result.content[0].text)
            assert comp["ok"] is True
            assert "ccr://" in comp["text"]
            cid = comp["ccr_id"]
            print("compress marker:", comp["text"].splitlines()[0][:58])

            pieces: list[str] = []
            offset = 0
            while True:
                ret_result = await session.call_tool(
                    "headroom_retrieve",
                    {"ccr_id": cid, "mode": "chunk", "offset": offset, "limit": 100},
                )
                page = json.loads(ret_result.content[0].text)
                assert page["ok"] is True
                pieces.append(page["content"])
                if not page["has_more"]:
                    break
                offset = page["next_offset"]
            assert "".join(pieces) == big, "E2E paginated roundtrip mismatch!"
            print(f"E2E paginated roundtrip OK: {len(big)} chars identical")

            stats_result = await session.call_tool("headroom_stats", {})
            stats = json.loads(stats_result.content[0].text)
            assert stats["ok"] is True and stats["calls"] == 1
            print("stats:", stats["calls"], "call(s)")
    print("stdio E2E PASS")


if __name__ == "__main__":
    asyncio.run(run())
