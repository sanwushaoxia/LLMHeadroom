"""日志压缩器测试。"""

from headroom.compressors.log import LogCompressor

LOG = "\n".join(
    [
        "2024-06-01 10:00:00.123 INFO  server started on :8080",
        "2024-06-01 10:00:01.001 DEBUG pool size=8",
        *[f"2024-06-01 10:00:{i:02d}.000 DEBUG healthcheck ok node-{i % 3}" for i in range(30)],
        "2024-06-01 10:00:40.000 WARN  slow query 1200ms id=1",
        "Traceback (most recent call last):",
        '  File "/app/main.py", line 100, in handler',
        '  File "/app/db.py", line 50, in query',
        '  File "/app/orm.py", line 30, in execute',
        '  File "/app/driver.py", line 20, in run',
        "psycopg2.errors.DeadlockDetected: deadlock detected",
        "2024-06-01 10:01:00.500 ERROR request failed id=55",
    ]
)


def test_detect_high_for_logs():
    c = LogCompressor()
    assert c.detect(LOG) >= 0.5


def test_detect_low_for_prose():
    c = LogCompressor()
    assert c.detect("这是一段普通文字。\n" * 5) < 0.3


def test_compress_folds_repeated_patterns():
    # 重复的 WARN 行(达到保留级别)应被模板折叠
    warn_log = "\n".join(
        [
            "2024-06-01 10:00:00.000 INFO  server started on :8080",
            *[f"2024-06-01 10:00:{i:02d}.000 WARN  timeout upstream svc retry {i}" for i in range(30)],
            "2024-06-01 10:01:00.500 ERROR request failed id=55",
        ]
    )
    out = LogCompressor().compress(warn_log)
    assert "同模式" in out and "×30" in out


def test_compress_filters_low_levels():
    out = LogCompressor(keep_level="WARN").compress(LOG)
    # DEBUG 心跳组整组省略,只在头锚点中保留首行样本
    assert out.count("healthcheck ok") <= 1
    assert "省略" in out
    # 高级别行保留
    assert "slow query" in out
    assert "ERROR request failed" in out


def test_compress_trims_stacktrace():
    out = LogCompressor().compress(LOG)
    assert "省略" in out and "堆栈" in out
    assert "DeadlockDetected" in out  # 异常行保留
    assert '"/app/orm.py"' not in out  # 中间帧被裁剪


def test_compress_keeps_anchor_lines():
    out = LogCompressor().compress(LOG)
    assert "server started on :8080" in out  # 首行锚点
    assert "ERROR request failed" in out  # 尾行锚点


# ------------------------------------------------- glog 格式


def _glog(severity, i, msg):
    return f"{severity}20260922 13:29:{i % 60:02d}.{i % 999999:06d} 19943 file.cc:{i % 200}] {msg}"


GLOG = "\n".join(
    [
        "WARNING: Logging before InitGoogleLogging() is written to STDERR",
        *[_glog("I", i, "processor ready") for i in range(30)],
        _glog("W", 40, "slow query"),
        _glog("E", 50, "request failed"),
    ]
)


def test_glog_extracts_severity_and_filters_info():
    out = LogCompressor(keep_level="WARN").compress(GLOG)
    assert "processor ready" not in out.split("[headroom] 原始日志头部锚点:")[1].split("---")[1]
    assert "省略" in out  # 30 行 glog INFO 被过滤
    assert "slow query" in out
    assert "request failed" in out


def test_glog_detect_routes():
    from headroom.core.router import ContentRouter

    assert LogCompressor().detect(GLOG) >= 0.5
    assert ContentRouter().select(GLOG).name == "log"


def test_glog_multiline_continuation_counts_toward_detect():
    # glog 记录后跟多行配置转储（非记录头），检测不应跌破阈值
    content = "\n".join(
        [
            *[_glog("I", i, "config dump follows") for i in range(10)],
            *["channel_configs {", "  enable_channel: true", "}", "key: value"],
        ]
    )
    assert LogCompressor().detect(content) >= 0.5
