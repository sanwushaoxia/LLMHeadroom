"""Headroom 演示:故障排查场景下的压缩与 CCR 取回。

运行:.venv/bin/python examples/demo.py

生成一份合成的大日志(含重复心跳、低级别噪音、堆栈跟踪),
交给 Headroom 压缩,打印实际压缩率,并用 marker 中的 ccr_id
验证 headroom_retrieve 取回的原文与原始输入逐字节一致。
"""

from __future__ import annotations

import random
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from headroom.core.pipeline import Headroom  # noqa: E402
from headroom.ccr.marker import extract_id  # noqa: E402


def build_synthetic_log(lines: int = 4000) -> str:
    """合成一份典型的故障排查日志:99% 心跳噪音 + 少量错误与堆栈。"""
    rng = random.Random(42)
    services = ["gateway", "orders", "users", "billing", "search"]
    parts: list[str] = ["2024-06-01T10:00:00.000Z INFO  service=main msg=boot complete"]
    for i in range(lines):
        t = f"2024-06-01T10:{i // 600:02d}:{(i // 10) % 60:02d}.{i % 10:03d}Z"
        svc = services[i % len(services)]
        if i % 97 == 0:  # 少量 ERROR
            parts.append(
                f"{t} ERROR service={svc} msg=upstream timeout req={rng.randint(100000, 999999)} latency={rng.randint(900, 5000)}ms"
            )
            parts.append("Traceback (most recent call last):")
            for depth in range(12):
                parts.append(f'  File "/app/svc/{svc}/layer_{depth}.py", line {100 + depth}, in handle_{depth}')
            parts.append(f"TimeoutError: upstream {svc} did not respond within 3000ms")
        elif i % 23 == 0:  # 少量 WARN
            parts.append(f"{t} WARN  service={svc} msg=slow query latency={rng.randint(200, 800)}ms")
        else:  # 心跳噪音
            parts.append(f"{t} DEBUG service={svc} msg=healthcheck ok seq={i}")
    parts.append("2024-06-01T10:59:59.999Z INFO  service=main msg=shutdown requested")
    return "\n".join(parts)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        # 演示用独立数据库,不污染 ~/.headroom
        import os

        os.environ["HEADROOM_DB"] = str(Path(tmp) / "demo.db")
        hr = Headroom()

        log = build_synthetic_log()
        orig_bytes = len(log.encode("utf-8"))

        result = hr.compress(log)
        s = result.stats

        print("=" * 62)
        print("Headroom 演示 — 故障排查日志压缩 + CCR 按需取回")
        print("=" * 62)
        print(f"压缩器        : {s.compressor}")
        print(f"原始大小      : {orig_bytes:,} B  (~{s.original_tokens:,} tokens)")
        print(f"压缩后        : {s.compressed_bytes:,} B  (~{s.compressed_tokens:,} tokens)")
        print(f"节省          : {s.saved_ratio:.1%}")
        print(f"CCR id        : {s.ccr_id}")
        print("-" * 62)
        print("压缩结果预览(前 500 字符):")
        print(result.text[:500])
        print("…")
        print("-" * 62)

        # 用 marker 中解析出的 id 取回,验证与原文逐字节一致
        cid_from_marker = extract_id(result.text)
        retrieved = hr.retrieve(cid_from_marker)
        assert retrieved == log, "CCR 取回内容与原文不一致!"
        print(f"✓ headroom_retrieve(ccr_id={cid_from_marker!r}) 取回 {len(retrieved):,} 字符,与原文逐字节一致")

        st = hr.stats()
        print(f"✓ 会话统计    : {st['calls']} 次调用,节省 {st['saved_ratio']:.1%} tokens")
        hr.close()
        print("=" * 62)


if __name__ == "__main__":
    main()
