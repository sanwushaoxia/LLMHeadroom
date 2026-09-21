"""测试公共夹具:临时数据库,避免污染 ~/.headroom。"""

from __future__ import annotations

import pytest

from headroom.core.models import Config
from headroom.core.pipeline import Headroom


@pytest.fixture(autouse=True)
def isolate_default_db(tmp_path, monkeypatch):
    """CLI/MCP tests must never write to ~/.headroom or read host config."""
    monkeypatch.setenv("HEADROOM_DB", str(tmp_path / "default.db"))
    monkeypatch.delenv("HEADROOM_CONFIG", raising=False)
    # 测试默认禁用项目备份回退；备份行为由专门测试显式启用。
    monkeypatch.setenv("HEADROOM_PROJECT_CONFIG", str(tmp_path / "no-backup.json"))
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))


@pytest.fixture()
def config(tmp_path):
    return Config(db_path=tmp_path / "ccr.db", ttl_hours=72.0, min_ratio=0.3)


@pytest.fixture()
def hr(config):
    engine = Headroom(config=config)
    yield engine
    engine.close()
