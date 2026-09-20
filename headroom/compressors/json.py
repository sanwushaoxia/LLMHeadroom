"""JSON 压缩器:长数组首尾采样 + 同构对象列表 schema 提炼 + 丢弃空值字段。"""

from __future__ import annotations

import json
from collections.abc import Sized
from typing import Any

from headroom.compressors.base import Compressor


class JsonCompressor(Compressor):
    name = "json"

    def __init__(self, sample_items: int = 3, drop_empty: bool = True, max_depth: int = 12):
        self.sample_items = sample_items
        self.drop_empty = drop_empty
        self.max_depth = max_depth

    def detect(self, content: str) -> float:
        s = content.strip()
        if not s or s[0] not in "[{" and s[-1] not in "]}":
            return 0.0
        try:
            json.loads(s)
        except (json.JSONDecodeError, ValueError):
            return 0.0
        return 1.0

    def compress(self, content: str) -> str:
        data = json.loads(content)
        out = self._node(data, depth=0, path="$")
        return json.dumps(out, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------ #

    def _node(self, node: Any, depth: int, path: str) -> Any:
        if self.drop_empty and isinstance(node, dict):
            node = {k: v for k, v in node.items() if v is not None and v != "" and v != [] and v != {}}

        if isinstance(node, dict):
            if depth >= self.max_depth:
                return f"<… 深度截断 {path}>"
            return {k: self._node(v, depth + 1, f"{path}.{k}") for k, v in node.items()}

        if isinstance(node, list):
            return self._list(node, depth, path)

        return node

    def _list(self, items: list, depth: int, path: str) -> Any:
        n = len(items)
        if n == 0:
            return []

        # 同构对象列表:提炼公共 schema + 差异字段
        if all(isinstance(x, dict) for x in items) and n >= 3:
            return self._homogeneous(items, depth, path)

        k = self.sample_items
        if n <= 2 * k:
            return [self._node(x, depth + 1, f"{path}[{i}]") for i, x in enumerate(items)]

        head = [self._node(x, depth + 1, f"{path}[{i}]") for i, x in enumerate(items[:k])]
        tail = [self._node(x, depth + 1, f"{path}[{i}]") for i, x in enumerate(items[-k:], start=n - k)]
        omitted = n - 2 * k
        marker = {
            "_headroom_omitted": omitted,
            "_note": f"中间省略 {omitted} 项(共 {n} 项);调用 headroom_retrieve 取回完整原文",
        }
        return [*head, marker, *tail]

    def _homogeneous(self, items: list, depth: int, path: str) -> Any:
        n = len(items)
        dicts: list[dict] = items  # type: ignore[assignment]
        all_keys: list[str] = []
        for d in dicts:
            for k in d:
                if k not in all_keys:
                    all_keys.append(k)

        shared = {k for k in all_keys if all(k in d and d[k] == next(x[k] for x in dicts if k in d) for d in dicts)}
        varying = [k for k in all_keys if k not in shared]

        k = self.sample_items
        sampled = (
            list(enumerate(dicts))
            if n <= 2 * k
            else [(i, dicts[i]) for i in range(k)] + [(n - k + i, dicts[n - k + i]) for i in range(k)]
        )

        out: list[dict] = [
            {
                "_headroom_schema": {
                    "keys": all_keys,
                    "shared": {k2: next(d[k2] for d in dicts if k2 in d) for k2 in sorted(shared)},
                    "varying_keys": varying,
                    "_note": f"同构对象列表共 {n} 项,已提炼公共字段;以下为首尾采样项(仅列差异字段)",
                }
            }
        ]
        for idx, d in sampled:
            diff = {k2: v for k2, v in d.items() if k2 in varying or not shared}
            out.append({"_index": idx, **diff})
        if n > 2 * k:
            omitted = n - 2 * k
            out.insert(
                1,
                {
                    "_headroom_omitted": omitted,
                    "_note": f"中间省略 {omitted} 项;调用 headroom_retrieve 取回完整原文",
                },
            )
        return out
