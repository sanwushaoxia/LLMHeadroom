# Headroom

Headroom 是一个面向 AI Agent 的上下文压缩层：通过 `ContentRouter` 自动选择日志、JSON、Python 代码或纯文本压缩器，并将压缩结果与 CCR（Context Compression with Retrieval）原文存档绑定。

压缩是可回溯的：结果可能带有 `[headroom:ccr://<id>]` 标记，Agent 可以调用 `headroom_retrieve` 分页取回完整 UTF-8 原文。CCR 原文使用 SQLite + zlib 存储，带 TTL、SHA-256 完整性校验、WAL 并发支持和容量限制。

## 核心机制

```text
原始内容
   │
   ▼
ContentRouter(hint 优先，否则 detect 置信度择优)
   ├── LogCompressor    重复模板 / 堆栈 / 低级别日志
   ├── JsonCompressor   长数组采样 / 同构列表 schema / 深度限制
   ├── CodeCompressor   Python 函数体折叠 / 安全 import 处理
   └── TextCompressor   重复行 / 空白规整兜底
   │
   ▼
压缩正文 + CCR marker ──► SQLite(zlib, TTL, SHA-256)
   │
   ▼
headroom_retrieve(ccr_id, offset, limit) ──► 原文分页取回
```

所有容量和 `*_bytes` 统计使用 UTF-8 字节数；token 数是 `len/4` 启发式，只用于相对比较，不是精确计费值。

## 安装

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest
```

### 激活虚拟环境

在项目根目录执行：

```bash
cd /home/wyh/Automation/Headroom
source .venv/bin/activate

# 激活后可以直接使用 python、pip 和 headroom
python -m pytest
headroom --version
```

完成后退出虚拟环境：

```bash
deactivate
```

不想激活时，也可以始终使用 `.venv/bin/python`、`.venv/bin/pip` 和 `.venv/bin/headroom`。

当前已验证 MCP SDK 2.x，依赖范围为 `mcp>=2.1,<3`。

## MCP Server

### Claude Code

```bash
claude mcp add headroom -- /path/to/.venv/bin/python -m headroom.mcp_server
```

### Cursor

```json
{
  "mcpServers": {
    "headroom": {
      "command": "/path/to/.venv/bin/python",
      "args": ["-m", "headroom.mcp_server"]
    }
  }
}
```

### ZCode

本项目提供 workspace 级 MCP 配置，文件位置为 `.zcode/config.json`。这样打开该项目时，ZCode 会自动连接 Headroom MCP；配置内容如下：

```json
{
  "mcp": {
    "servers": {
      "headroom": {
        "type": "stdio",
        "command": "/home/wyh/Automation/Headroom/.venv/bin/python",
        "args": ["-m", "headroom.mcp_server"],
        "enabled": true,
        "timeoutMs": 60000
      }
    }
  }
}
```

如果项目路径不同，把 `command` 改为项目实际路径下的 `.venv/bin/python`。也可以在 ZCode 的 **Settings → MCP** 中添加同样的 stdio server。workspace 配置只对本项目生效；如果要对所有项目生效，将相同配置放到 `~/.zcode/cli/config.json` 的 `mcp.servers` 下。

验证 MCP 是否启动：

```bash
cd /home/wyh/Automation/Headroom
source .venv/bin/activate
python examples/e2e_mcp_check.py
```



#### `headroom_compress(content, hint?)`

返回 JSON envelope：

```json
{
  "schema_version": 1,
  "ok": true,
  "text": "[headroom:ccr://...] ...",
  "compressor": "log",
  "ccr_id": "完整 32 位 hex id 或 null",
  "retrievable": true,
  "original_bytes": 14399,
  "compressed_bytes": 600,
  "emitted_bytes": 620,
  "saved_ratio": 0.956,
  "lossy": true,
  "omissions": [],
  "warnings": []
}
```

`hint` 可选：`log`、`json`、`code`、`text`。未知 hint 返回 `UNKNOWN_HINT`。

#### `headroom_retrieve(ccr_id, mode, offset, limit)`

`mode` 支持 `full`、`preview`、`chunk`。大内容建议使用 `chunk`：

```json
{
  "schema_version": 1,
  "ok": true,
  "ccr_id": "...",
  "content": "原文片段",
  "offset": 0,
  "limit": 20000,
  "total_chars": 14399,
  "total_bytes": 14399,
  "next_offset": 20000,
  "has_more": true,
  "content_hash": "sha256..."
}
```

`offset` 和 `limit` 使用 Unicode 字符位置；`content_hash` 是完整原文 UTF-8 bytes 的 SHA-256。

#### `headroom_stats()`

返回 SQLite 数据库范围内的持久化累计统计，以及当前未过期 CCR 库存。新进程和 CLI 都能读取同一累计统计。

### 错误 envelope

错误不会再用自然语言字符串表示：

```json
{
  "schema_version": 1,
  "ok": false,
  "error": {
    "code": "CCR_NOT_FOUND",
    "message": "CCR entry not found",
    "details": {"ccr_id": "..."}
  }
}
```

常见错误码：`CONTENT_TOO_LARGE`、`CCR_NOT_FOUND`、`CCR_INTEGRITY_ERROR`、`INVALID_MODE`、`UNKNOWN_HINT`、`STORE_CAPACITY_EXCEEDED`、`CONFIG_ERROR`。

## Python API

```python
from headroom.core.pipeline import Headroom

hr = Headroom()
try:
    result = hr.compress(big_log_text, hint="log")
    print(result.text)
    print(result.stats.as_dict())

    if result.stats.ccr_id:
        page = hr.retrieve_page(result.stats.ccr_id, offset=0, limit=20_000)
        print(page.content)
finally:
    hr.close()
```

超过 `HEADROOM_MAX_CONTENT` 的输入会抛 `ContentTooLargeError`，不会静默截断。CCR 取回会校验原文 byte length 和 SHA-256；数据库 payload 损坏时抛 `CCRIntegrityError`。

## 日志筛选脚本

项目根目录提供两个轻量日志筛选工具：

- [`filter_log.sh`](./filter_log.sh)：使用系统 `grep`，适合快速筛选大文件。
- [`filter_log.py`](./filter_log.py)：Python 实现，支持 `--encoding`，适合跨平台或需要在脚本中复用。

### Shell 版本

```bash
# 激活虚拟环境不是必须的；该脚本只依赖 bash 和 grep
./filter_log.sh input/app.log ERROR

# 指定输出文件
./filter_log.sh input/app.log ERROR output/error.log

# 忽略大小写，或反向筛选不包含 ERROR 的行
./filter_log.sh -i input/app.log error output/error.log
./filter_log.sh -v input/app.log DEBUG output/non_debug.log
```

不指定输出文件时，输出为输入文件同目录下的 `<文件名>.filtered.log`。关键字按固定字符串匹配，不是正则表达式；关键字以 `-` 开头时也可以正常使用。

### Python 版本

```bash
# 激活环境后
python filter_log.py input/app.log ERROR output/error.log
python filter_log.py input/app.log error output/error.log --ignore-case
python filter_log.py input/app.log DEBUG output/non_debug.log --invert

# 不激活环境时
.venv/bin/python filter_log.py input/app.log ERROR output/error.log
```

Python 版本也支持短参数 `-i`/`-v`，并可以指定输入编码：

```bash
python filter_log.py input/app.log ERROR output/error.log --encoding gb18030
```

### 与 Headroom 联用

先筛掉无关日志，再让 Headroom 自动压缩：

```bash
mkdir -p output
./filter_log.sh input/app.log ERROR output/error.log
headroom compress output/error.log --hint log --json > output/error.compressed.json
```

也可以直接把筛选结果通过 stdin 交给 Headroom：

```bash
./filter_log.sh input/app.log ERROR | headroom compress - --hint log
```

`filter_log.sh` 会输出统计信息到 stderr，因此可以安全地把筛选后的日志通过 stdout 管道给 Headroom。

## CLI

```bash
# 人类可读输出
headroom compress app.log --hint log
headroom compress app.log --encoding latin-1 --hint log
headroom compress large.log --max-content 10485760 --hint log
headroom retrieve <ccr_id> --offset 0 --limit 20000
headroom stats
headroom mcp

# 稳定 JSON 输出
headroom compress app.log --json
headroom retrieve <ccr_id> --offset 0 --limit 1000 --json
headroom stats --json

# 无损 TXT 日志转 JSON（不压缩、不写 CCR）
headroom convert app.log --from txt --to json > app.jsonl
headroom convert app.log --from txt --to json --format array > app.json
headroom convert - --from txt --to json --encoding latin-1
```

### TXT 日志转换为 JSON

`convert` 子命令是无损日志解析：不执行 Headroom 压缩，不创建 CCR 条目，也不影响压缩统计。默认输出 JSONL（每行一个 JSON 对象，适合大日志流式处理）；需要单个 JSON 值时使用 `--format array`。

```bash
headroom convert app.log --from txt --to json > app.jsonl
headroom convert app.log --from txt --to json --format array > app.json
headroom convert - --from txt --to json --encoding latin-1
```

可用参数：`--format {jsonl,array}`（默认 `jsonl`）、`--encoding`（遵循 CLI 配置文件，缺省 `utf-8`）、`--output`（默认 stdout）、`--no-raw`（省略原始文本以减小输出）。

解析出的日志对象字段为 `type`、`timestamp`、`level`、`process_id`、`service`、`thread_id`、`source_file`、`source_line`、`message`、`continuation`、`line_start`、`line_end` 和 `raw`。时间戳保留原始字符串，不猜测日期或时区；多行内容（堆栈、缩进诊断块）归入前一记录的 `continuation`；无法安全解析的标题和损坏行输出为 `type: "unparsed"`，绝不静默丢失。

注意：`convert` 的输出是日志记录本身，不是 `compress --json` 的响应 envelope；两者语义不同。

CLI 默认严格按 UTF-8 读取输入。若日志来自单字节编码或包含无法按 UTF-8 解码的历史字节，可显式指定编码，例如：

```bash
headroom compress output/workflow.log --encoding latin-1 --hint log
```

### CLI 配置文件

不想每次输入 `--encoding`、`--max-content`、`--hint` 时，可把压缩默认值保存到 `~/.headroom/config.json`：

```json
{
  "encoding": "latin-1",
  "max_content": 10485760,
  "hint": "log"
}
```

之后命令即可简化为：

```bash
headroom compress output/workflow.log > output/workflow.compressed.log
```

配置文件规则：

- 路径优先级：`compress --config PATH` > `HEADROOM_CONFIG` > `~/.headroom/config.json`。
- `~/.headroom/config.json` 不存在时，若项目仓库根目录有 `config.json`（见本项目根目录的备份模板），会自动使用它并另存一份到 `~/.headroom/`。
- 参数优先级：CLI 显式参数 > `HEADROOM_MAX_CONTENT` 环境变量 > JSON `max_content` > 内置 `2 MiB`；`encoding` 和 `hint` 只由 CLI 参数与 JSON 控制。
- 只接受 `encoding`（合法 codec 名）、`max_content`（正整数）、`hint`（`log`/`json`/`code`/`text` 或 `null`）；坏 JSON、未知字段或非法值返回 `CONFIG_ERROR`。
- 项目仓库的 `config.json` 是 CLI 压缩默认值模板；`.zcode/config.json` 是 ZCode MCP 配置，两者用途不同，不要混用。
- 团队共享配置可提交一份 JSON，并显式指定：`headroom compress --config ./headroom-config.json app.log`。
- `max_content` 按解码后文本的 UTF-8 bytes 计算；`latin-1` 会把每个 0–255 字节映射成一个字符，适合编码未知但必须完成筛选/压缩的历史日志。若知道真实编码，优先用 `gb18030`、`cp1252` 等。

默认 UTF-8 解码失败时，CLI 返回 `INPUT_DECODE_ERROR`，不会静默替换坏字节。


`--max-content` 只对本次 CLI 调用生效，例如处理 6.8 MB 日志：

```bash
headroom compress output/workflow.log \
  --encoding latin-1 \
  --max-content 10485760 \
  --hint log \
  > output/workflow.compressed.log
```

## 配置

| 环境变量 | 默认值 | 说明 |
|---|---:|---|
| `HEADROOM_DB` | `~/.headroom/ccr.db` | SQLite 路径 |
| `HEADROOM_TTL_HOURS` | `72` | CCR TTL |
| `HEADROOM_MIN_RATIO` | `0.3` | 触发 CCR 的最低正文节省比例 |
| `HEADROOM_MAX_CONTENT` | `2097152` | 单次输入最大 UTF-8 bytes，超限拒绝 |
| `HEADROOM_MAX_STORE_BYTES` | `268435456` | CCR 压缩 payload 逻辑总容量 |
| `HEADROOM_MAX_STORE_ENTRIES` | `10000` | CCR 最大活跃条目数 |
| `HEADROOM_RETRIEVE_DEFAULT_LIMIT` | `20000` | 默认分页大小 |
| `HEADROOM_CONFIG` | 无 | 覆盖 CLI 配置文件路径 |

容量上限只针对 SQLite 中压缩 BLOB 的逻辑大小，不等同 `.db`、`.db-wal` 和 `.db-shm` 的物理文件大小。

## 压缩器与 loss metadata

压缩器保留兼容的 `compress() -> str`，并支持 `compress_with_metadata()` 返回 `CompressionOutput`：

- `lossy`：是否省略/变换内容
- `omissions`：类型、路径/行号、数量和原因
- `language`：`log`、`json`、`python` 或 `text`

首版代码压缩器只承诺 Python，并确保折叠后可通过 `ast.parse`。JSON 输出使用 `_headroom` envelope，避免把元数据混入用户对象字段；JSON 采样、空字段删除和深度截断都会记录 metadata。

## 测试与演示

```bash
.venv/bin/python -m pytest
.venv/bin/python examples/demo.py
.venv/bin/python examples/e2e_mcp_check.py
```

测试覆盖 UTF-8/超限、Config 校验、TTL、持久统计、SQLite hash/容量/并发、marker、分页 MCP、CLI JSON、日志/JSON/Python/文本压缩边界。

## 项目结构

```text
headroom/
├── core/          # errors / models / router / pipeline
├── compressors/   # log / json / code(Python) / text
├── ccr/           # SQLiteStore / marker
├── integrations/  # MCP Server
├── log_conversion.py  # 无损 TXT 日志 → JSON/JSONL 解析
└── cli.py
```

HTTP Proxy、Agent Hooks、embedding 语义压缩和中文专用模型仍属于后续扩展范围；当前可通过 `router.register()` 添加自定义压缩器。
