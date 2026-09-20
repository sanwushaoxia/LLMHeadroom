# Headroom

通用上下文压缩层:多种专用压缩器 + ContentRouter 自动路由 + CCR(压缩后可按需取回原文)。
面向 AI Agent 场景(故障排查、日志分析、长 JSON/代码上下文),显著降低 token 消耗,同时**不丢失信息**——LLM 随时可凭标记取回原文。

## 核心机制

```
原始内容 ──► ContentRouter(按置信度自动选路)
                │
                ├─ LogCompressor    重复行折叠 / 堆栈裁剪 / 低级别过滤
                ├─ JsonCompressor   长数组首尾采样 / 同构列表 schema 提炼
                ├─ CodeCompressor   函数体折叠 / import 合并
                └─ TextCompressor   兜底:重复行 / 空白规整
                        │
                        ▼
        压缩结果 + CCR 标记 ──► SQLite 存储(zlib, TTL 72h)
                        │
                        ▼
     LLM 调用 headroom_retrieve(ccr_id) ──► 取回完整原文
```

**CCR (Context Compression with Retrieval)** 是 Headroom 的核心差异:压缩不是单向摘要,而是"按需查阅"。压缩结果首行附带标记:

```
[headroom:ccr://d40fb722|orig=330846B|saved=89%] 原文已压缩存档,可调用 headroom_retrieve(ccr_id="d40fb722") 取回完整原文。
```

LLM 看到标记后,任何时刻都可以取回原文,不用担心摘要丢失关键细节。

实测(本仓库 `examples/demo.py`,4000 行合成故障排查日志):**节省 89.6% token,取回内容与原文逐字节一致**。实际节省比例取决于输入形态。

## 安装

```bash
python3 -m venv .venv
.venv/bin/pip install -e .

# 激活虚拟环境后即可直接使用 headroom 命令
source .venv/bin/activate
headroom compress app.log

# 或者不激活，直接调用
.venv/bin/headroom compress app.log
```

> 提示：若在 conda 环境下（如 base）使用，务必先 `source .venv/bin/activate` 再运行 `headroom`——conda base 中没有安装该命令，否则会报 `headroom: command not found`。

## 作为 MCP Server 接入 Claude Code / Cursor

```bash
# Claude Code
claude mcp add headroom -- /path/to/.venv/bin/python -m headroom.mcp_server

# Cursor(mcp.json)
{
  "mcpServers": {
    "headroom": {
      "command": "/path/to/.venv/bin/python",
      "args": ["-m", "headroom.mcp_server"]
    }
  }
}
```

提供 3 个工具:

| 工具 | 说明 |
|---|---|
| `headroom_compress(content, hint?)` | 压缩长内容,自动路由;结果带 CCR 标记 |
| `headroom_retrieve(ccr_id, mode?)` | 按需取回原文(`full` / `preview`) |
| `headroom_stats()` | 压缩统计与 CCR 库存 |

## Python API

```python
from headroom.core.pipeline import Headroom

hr = Headroom()
result = hr.compress(big_log_text)
print(result.text)                # 压缩结果(首行带 CCR 标记)
print(result.stats.saved_ratio)   # 节省比例
print(hr.retrieve(result.stats.ccr_id))  # 取回原文(与输入逐字节一致)
```

扩展自定义压缩器:

```python
from headroom.compressors.base import Compressor
from headroom.core.pipeline import Headroom

class MyCompressor(Compressor):
    name = "my"
    def detect(self, content): return 0.9 if "<csv>" in content else 0.0
    def compress(self, content): return content[:1000] + "…"

hr = Headroom()
hr.router.register(MyCompressor())   # 插入到 text 兜底之前
```

## CLI

```bash
headroom compress app.log     # 压缩文件(- 为 stdin)
headroom retrieve d40fb722    # 按 CCR id 取回原文
headroom stats                # 查看统计
headroom mcp                  # 启动 MCP server(stdio)
```

## 配置(环境变量)

| 变量 | 默认 | 说明 |
|---|---|---|
| `HEADROOM_DB` | `~/.headroom/ccr.db` | CCR 存储路径 |
| `HEADROOM_TTL_HOURS` | `72` | 原文存档存活时间 |
| `HEADROOM_MIN_RATIO` | `0.3` | 触发 CCR 的最低节省比例 |
| `HEADROOM_MAX_CONTENT` | `2097152` | 单次输入最大字节数 |

## 项目结构

```
headroom/
├── core/
│   ├── models.py      # Config / CompressResult / Stats / token 估算
│   ├── router.py      # ContentRouter:按 detect() 置信度择优
│   └── pipeline.py    # Headroom 门面:compress / retrieve / stats
├── compressors/       # log / json / code / text(兜底)
├── ccr/               # SQLiteStore(zlib+TTL)/ marker 嵌入与解析
├── integrations/      # MCP server(stdio)
└── cli.py             # compress / retrieve / stats / mcp
```

## 测试与演示

```bash
.venv/bin/python -m pytest          # 52 个测试
.venv/bin/python examples/demo.py   # 故障排查场景演示
```

## 设计取舍与边界

- 首版压缩算法为**规则式**(确定性、可测试、零额外依赖);语义/embedding 类压缩未包含,可通过 `router.register()` 扩展。
- token 估算采用 `len/4` 启发式,未引入 tokenizer 依赖;数字用于相对比较,非精确计费。
- HTTP Proxy / Agent Wrap(hooks)集成模式、中文压缩模型不在首版范围,架构上已预留扩展点。
- CCR 存档默认 72 小时后过期,过期后 `headroom_retrieve` 返回明确提示(不可恢复)。
