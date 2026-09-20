"""测试公共夹具:临时数据库,避免污染 ~/.headroom。"""

from __future__ import annotations

import pytest

from headroom.core.models import Config
from headroom.core.pipeline import Headroom


@pytest.fixture()
def config(tmp_path):
    return Config(db_path=tmp_path / "ccr.db", ttl_hours=72.0, min_ratio=0.3)


@pytest.fixture()
def hr(config):
    engine = Headroom(config=config)
    yield engine
    engine.close()
