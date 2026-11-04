from pathlib import Path

import pytest
from typer.testing import CliRunner

from ces_youtube_tools.cli import APP, list_cmd
from ces_youtube_tools.ytdlp import YtDlp
from ces_youtube_tools.config import AppConfig, save_config, ChannelConfig
from ces_youtube_tools.youtube import VideoSummary

from .conftest import CHANNEL

RUNNER = CliRunner()


def test_list(config_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = AppConfig(channels=[CHANNEL])
    config.auth.firefox_profile = Path("/p/abc.Profile 1")
    save_config(config, config_path)
    seen: list[tuple[str, str | None, int]] = []

    def fake_list(channel: ChannelConfig, ytdlp: YtDlp, limit: int) -> list[VideoSummary]:
        seen.append((channel.channel_id, ytdlp.cookies_spec, limit))
        return [
            VideoSummary(id="abc", title="A members stream", duration=3940, live_status="was_live", members_only=True)
        ]

    monkeypatch.setattr(list_cmd, "list_latest_videos", fake_list)
    result = RUNNER.invoke(
        APP, ["list", "3BLUE1BROWN", "-n", "5", "--config", str(config_path)], env={"COLUMNS": "200"}
    )
    assert result.exit_code == 0, result.output
    assert seen == [("UCYO", None, 5)]  # listing never sends cookies
    assert "A members stream" in result.output
    assert "members stream" in result.output
    assert "1:05:40" in result.output


def test_list_unknown_channel(config_path: Path) -> None:
    save_config(AppConfig(channels=[CHANNEL]), config_path)
    result = RUNNER.invoke(APP, ["list", "nope", "--config", str(config_path)], env={"COLUMNS": "200"})
    assert result.exit_code == 1
    assert "Configured channels: 3Blue1Brown" in result.output


def test_list_no_videos(config_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    save_config(AppConfig(channels=[CHANNEL]), config_path)
    monkeypatch.setattr(list_cmd, "list_latest_videos", lambda channel, ytdlp, limit: [])
    result = RUNNER.invoke(APP, ["list", "3blue1brown", "--config", str(config_path)])
    assert result.exit_code == 0
    assert "No videos found" in result.output
