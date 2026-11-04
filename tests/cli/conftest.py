from pathlib import Path

import pytest

from ces_youtube_tools.config import ChannelConfig

CHANNEL = ChannelConfig(name="3Blue1Brown", url="https://www.youtube.com/@3blue1brown", channel_id="UCYO")


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    return tmp_path / "config" / "config.toml"
