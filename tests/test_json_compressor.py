"""JSON 压缩器测试。"""

import json

from headroom.compressors.json import JsonCompressor

BIG_LIST = json.dumps(
    [
        {"id": i, "name": f"item-{i}", "status": "active", "score": i * 1.5}
        for i in range(100)
    ]
)


def test_detect_valid_json():
    assert JsonCompressor().detect(BIG_LIST) == 1.0
    assert JsonCompressor().detect('{"a": 1}') == 1.0


def test_detect_rejects_non_json():
    c = JsonCompressor()
    assert c.detect("plain text log line") == 0.0
    assert c.detect('{"broken": ') == 0.0


def test_long_array_sampled_head_tail():
    out = JsonCompressor(sample_items=3).compress(BIG_LIST)
    data = json.loads(out)
    assert len([x for x in data if isinstance(x, dict) and "id" in x]) == 6  # 首 3 + 尾 3
    markers = [x for x in data if isinstance(x, dict) and x.get("_headroom", {}).get("kind") == "array_sample"]
    assert markers and markers[0]["_headroom"]["omitted"] == 94


def test_homogeneous_list_schema():
    out = JsonCompressor(sample_items=2).compress(BIG_LIST)
    data = json.loads(out)
    schema = next(x for x in data if x.get("_headroom", {}).get("kind") == "object_schema")
    assert set(schema["_headroom"]["schema"]["shared"]) == {"status"}
    assert set(schema["_headroom"]["schema"]["varying_keys"]) == {"id", "name", "score"}


def test_nested_object_compressed():
    payload = json.dumps({"user": {"name": "a", "nickname": None}, "rows": list(range(50))})
    out = JsonCompressor(sample_items=2).compress(payload)
    data = json.loads(out)
    assert "nickname" not in data["user"]  # 空值字段丢弃
    assert len(data["rows"]) == 5  # 首 2 + marker + 尾 2


def test_compression_actually_saves():
    out = JsonCompressor().compress(BIG_LIST)
    assert len(out) < len(BIG_LIST)
