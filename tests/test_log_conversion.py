"""Tests for lossless text-log conversion."""

from __future__ import annotations

import io
import json

import pytest

from headroom.log_conversion import convert_stream, iter_log_records, parse_log_line, txt_to_json


SAMPLE = (
    "21:20:50.026878 WARN [pid358547/ts_e2e_e2e][] tid=358554 "
    "robot_kinematics_model.cc:149 Joint 'left_gripper_joint' not found."
)


def test_parse_canonical_log_line():
    parsed = parse_log_line(SAMPLE)
    assert parsed is not None
    assert parsed.timestamp == "21:20:50.026878"
    assert parsed.level == "WARN"
    assert parsed.process_id == 358547
    assert parsed.service == "ts_e2e_e2e"
    assert parsed.thread_id == 358554
    assert parsed.source_file == "robot_kinematics_model.cc"
    assert parsed.source_line == 149
    assert parsed.message == "Joint 'left_gripper_joint' not found."
    assert parsed.raw == SAMPLE


@pytest.mark.parametrize(
    ("line", "timestamp", "level"),
    [
        ("2024-01-01T00:00:00.123Z ERROR failed", "2024-01-01T00:00:00.123Z", "ERROR"),
        ("Jan  2 03:04:05 info message", "Jan  2 03:04:05", "INFO"),
        ("[2024-01-01 00:00:00] warning message", "[2024-01-01 00:00:00]", "WARNING"),
        ("03:04:05 debug message", "03:04:05", "DEBUG"),
    ],
)
def test_parse_timestamp_and_level_variants(line, timestamp, level):
    parsed = parse_log_line(line)
    assert parsed is not None
    assert parsed.timestamp == timestamp
    assert parsed.level == level


def test_optional_fields_and_message_are_preserved():
    parsed = parse_log_line("12:00:00 INFO message has [ERROR] and : punctuation")
    assert parsed is not None
    assert parsed.process_id is None
    assert parsed.thread_id is None
    assert parsed.source_file is None
    assert parsed.source_line is None
    assert parsed.message == "message has [ERROR] and : punctuation"


def test_continuations_and_unparsed_blocks_are_lossless():
    content = "\n".join(
        [
            "# dump header",
            "header detail",
            SAMPLE,
            "Pose refinement report",
            "    Residuals : 8",
            "12:00:00 INFO next.cc:3 done",
            "12:00:01 BROKEN record",
            "trailing header",
        ]
    )
    records = list(iter_log_records(content.splitlines(keepends=True)))
    assert records[0].as_dict()["type"] == "unparsed"
    assert records[0].as_dict()["raw"] == "# dump header\nheader detail"
    first = records[1].as_dict()
    assert first["type"] == "log"
    assert first["line_start"] == 3
    assert first["line_end"] == 5
    assert first["continuation"] == ["Pose refinement report", "    Residuals : 8"]
    assert records[2].as_dict()["message"] == "done"
    malformed = records[3].as_dict()
    assert malformed["type"] == "unparsed"
    assert malformed["raw"] == "12:00:01 BROKEN record\ntrailing header"


def test_jsonl_and_array_output_are_valid_and_keep_unicode():
    content = SAMPLE + " 消息\n"
    jsonl = txt_to_json(content)
    lines = jsonl.splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["message"].endswith(" 消息")
    assert "消息" in lines[0]

    array = json.loads(txt_to_json(content, output_format="array"))
    assert len(array) == 1
    assert array[0]["raw"] == content.rstrip("\n")
    assert txt_to_json("", output_format="array") == "[]"


def test_no_raw_and_stream_array():
    output = io.StringIO()
    convert_stream(io.StringIO(SAMPLE + "\n"), output, output_format="array", include_raw=False)
    payload = json.loads(output.getvalue())
    assert "raw" not in payload[0]


def test_invalid_output_format():
    with pytest.raises(ValueError, match="output_format"):
        txt_to_json("", output_format="invalid")


def test_crlf_and_no_final_newline_preserve_record_text():
    content = SAMPLE + "\r\n"
    payload = json.loads(txt_to_json(content, output_format="array"))
    assert payload[0]["raw"] == SAMPLE


GLOG_SAMPLE = (
    "I20260922 13:29:51.721504 19943 module_argument.cc:124] "
    "[][UNKNOWN]command: mainboard -d quickdata_awr.dag"
)


def test_parse_glog_line_fields():
    parsed = parse_log_line(GLOG_SAMPLE)
    assert parsed is not None
    assert parsed.timestamp == "20260922 13:29:51.721504"
    assert parsed.level == "INFO"
    assert parsed.thread_id == 19943
    assert parsed.source_file == "module_argument.cc"
    assert parsed.source_line == 124
    assert parsed.message == "[][UNKNOWN]command: mainboard -d quickdata_awr.dag"


@pytest.mark.parametrize(
    ("severity", "level"),
    [("I", "INFO"), ("W", "WARNING"), ("E", "ERROR"), ("F", "FATAL")],
)
def test_parse_glog_severity_mapping(severity, level):
    parsed = parse_log_line(f"{severity}20260922 13:29:51.721504 19943 x.cc:1] msg")
    assert parsed is not None
    assert parsed.level == level


def test_glog_multiline_becomes_continuation():
    content = "\n".join(
        [
            GLOG_SAMPLE,
            "channel_configs {",
            "  enable_channel: true",
            "}",
            "W20260922 13:30:00.000001 19950 x.cc:1] oops",
        ]
    )
    records = list(iter_log_records(content.splitlines(keepends=True)))
    assert len(records) == 2
    first = records[0].as_dict()
    assert first["level"] == "INFO"
    assert first["continuation"] == ["channel_configs {", "  enable_channel: true", "}"]
    assert first["line_start"] == 1
    assert first["line_end"] == 4
    assert records[1].as_dict()["level"] == "WARNING"
