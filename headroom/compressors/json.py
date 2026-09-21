"""JSON 压缩器:采样长数组、提炼同构列表，并输出结构化省略报告。"""

from __future__ import annotations

import json
import math
from typing import Any

from headroom.compressors.base import CompressionOutput, Compressor, Omission


class JsonCompressor(Compressor):
    name = "json"

    def __init__(self, sample_items: int = 3, drop_empty: bool = True, max_depth: int = 12):
        if sample_items <= 0:
            raise ValueError("sample_items must be greater than zero")
        if max_depth <= 0:
            raise ValueError("max_depth must be greater than zero")
        self.sample_items = sample_items
        self.drop_empty = drop_empty
        self.max_depth = max_depth

    def detect(self, content: str) -> float:
        s = content.strip()
        if not s:
            return 0.0
        try:
            json.loads(s, parse_constant=self._reject_constant)
        except (json.JSONDecodeError, ValueError):
            return 0.0
        return 1.0

    @staticmethod
    def _reject_constant(value: str) -> Any:
        raise ValueError(f"non-standard JSON constant: {value}")

    def compress_with_metadata(self, content: str) -> CompressionOutput:
        data = json.loads(content, parse_constant=self._reject_constant)
        omissions: list[Omission] = []
        out = self._node(data, depth=0, path="", omissions=omissions)
        return CompressionOutput(
            text=json.dumps(out, ensure_ascii=False, indent=2),
            lossy=bool(omissions),
            omissions=tuple(omissions),
            language="json",
        )

    def compress(self, content: str) -> str:
        return self.compress_with_metadata(content).text

    def _node(self, node: Any, depth: int, path: str, omissions: list[Omission]) -> Any:
        if depth >= self.max_depth and isinstance(node, (dict, list)):
            omissions.append(
                Omission(
                    kind="depth",
                    reason="nested JSON container exceeded max_depth",
                    path=path or "/",
                )
            )
            return {"_headroom": {"kind": "depth", "path": path or "/"}}

        if isinstance(node, dict):
            output: dict[str, Any] = {}
            for key, value in node.items():
                child_path = f"{path}/{self._escape_pointer(key)}"
                if self.drop_empty and value is None or self.drop_empty and value in ("", [], {}):
                    omissions.append(
                        Omission(
                            kind="empty_field",
                            reason="removed null or empty JSON field",
                            path=child_path,
                            count=1,
                        )
                    )
                    continue
                output[key] = self._node(value, depth + 1, child_path, omissions)
            return output

        if isinstance(node, list):
            return self._list(node, depth, path, omissions)

        return node

    @staticmethod
    def _escape_pointer(value: str) -> str:
        return str(value).replace("~", "~0").replace("/", "~1")

    def _list(self, items: list[Any], depth: int, path: str, omissions: list[Omission]) -> Any:
        n = len(items)
        if n == 0:
            return []
        if all(isinstance(item, dict) for item in items) and n >= 3:
            return self._homogeneous(items, depth, path, omissions)
        k = self.sample_items
        if n <= 2 * k:
            return [self._node(item, depth + 1, f"{path}/{i}", omissions) for i, item in enumerate(items)]
        head = [self._node(item, depth + 1, f"{path}/{i}", omissions) for i, item in enumerate(items[:k])]
        tail = [
            self._node(item, depth + 1, f"{path}/{i}", omissions)
            for i, item in enumerate(items[-k:], start=n - k)
        ]
        omitted = n - 2 * k
        omissions.append(
            Omission(
                kind="array_sample",
                reason="sampled head and tail of long JSON array",
                path=path or "/",
                count=omitted,
                details={"total": n, "sample_items": k},
            )
        )
        # Envelope key is namespaced and cannot collide with an original item because
        # this object is a dedicated metadata item, not a user's object field.
        marker = {
            "_headroom": {
                "kind": "array_sample",
                "omitted": omitted,
                "total": n,
                "note": "call headroom_retrieve to fetch the original",
            }
        }
        return [*head, marker, *tail]

    def _homogeneous(
        self,
        items: list[dict[str, Any]],
        depth: int,
        path: str,
        omissions: list[Omission],
    ) -> Any:
        n = len(items)
        all_keys: list[str] = []
        seen: set[str] = set()
        for item in items:
            for key in item:
                if key not in seen:
                    seen.add(key)
                    all_keys.append(key)

        shared: dict[str, Any] = {}
        varying: list[str] = []
        optional: list[str] = []
        for key in all_keys:
            present = [item[key] for item in items if key in item]
            if len(present) < n:
                optional.append(key)
                varying.append(key)
            elif all(value == present[0] for value in present[1:]):
                shared[key] = self._node(
                    present[0], depth + 1, f"{path}/_headroom/shared/{self._escape_pointer(key)}", omissions
                )
            else:
                varying.append(key)

        k = self.sample_items
        indexes = list(range(n)) if n <= 2 * k else [*range(k), *range(n - k, n)]
        omitted = max(0, n - len(indexes))
        if omitted:
            omissions.append(
                Omission(
                    kind="object_array_sample",
                    reason="sampled head and tail of homogeneous object array",
                    path=path or "/",
                    count=omitted,
                    details={"total": n, "sample_items": k},
                )
            )

        schema = {
            "keys": all_keys,
            "shared": shared,
            "varying_keys": varying,
            "optional_keys": optional,
            "total": n,
        }
        output: list[Any] = [{"_headroom": {"kind": "object_schema", "schema": schema}}]
        if omitted:
            output.append({"_headroom": {"kind": "array_sample", "omitted": omitted, "total": n}})
        for index in indexes:
            item = items[index]
            diff: dict[str, Any] = {"_index": index}
            for key, value in item.items():
                if key in varying:
                    diff[key] = self._node(value, depth + 1, f"{path}/{index}/{self._escape_pointer(key)}", omissions)
            output.append(diff)
        return output
