"""端到端验收:真实 stdio 子进程 + 官方 MCP client 会话。

流程:spawn `python -m headroom.mcp_server` → initialize → tools/list →
headroom_compress → headroom_retrieve → 校验取回内容与原文逐字节一致。
"""

import asyncio
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
            names = {t.name for t in tools.tools}
            assert names == {"headroom_compress", "headroom_retrieve", "headroom_stats"}, names
            print("tools:", sorted(names))

            big = "\n".join(
                f"2024-01-01 00:{i // 60:02d}:{i % 60:02d} DEBUG healthcheck ok node-{i % 5}"
                for i in range(300)
            )
            comp = await session.call_tool("headroom_compress", {"content": big})
            text = comp.content[0].text
            assert "ccr://" in text, text
            cid = text.split("ccr://")[1][:8]
            print("compress marker:", text.splitlines()[0][:58])

            ret = await session.call_tool("headroom_retrieve", {"ccr_id": cid})
            assert ret.content[0].text == big, "E2E roundtrip mismatch!"
            print(f"E2E roundtrip OK: {len(big)} chars identical")

            st = await session.call_tool("headroom_stats", {})
            print(st.content[0].text)
    print("stdio E2E PASS")


if __name__ == "__main__":
    asyncio.run(run())
