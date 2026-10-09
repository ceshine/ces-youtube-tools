from pathlib import Path

import pytest
from typer.testing import CliRunner

from youtube_tools import cli
from youtube_tools.cli import APP
from youtube_tools.ytdlp import YtDlp
from youtube_tools.config import AppConfig, load_config, save_config, ChannelConfig

RUNNER = CliRunner()
CHANNEL = ChannelConfig(name="3Blue1Brown", url="https://www.youtube.com/@3blue1brown", channel_id="UCYO")


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    return tmp_path / "config" / "config.toml"


@pytest.fixture
def saved_config(config_path: Path) -> AppConfig:
    config = AppConfig()
    config.auth.firefox_profile = Path("/p/abc.Profile 1")
    save_config(config, config_path)
    return config


def test_help_runs() -> None:
    result = RUNNER.invoke(APP, ["--help"])
    assert result.exit_code == 0
    assert "watching guides" in result.output


def test_init_defaults_to_first_signed_in_profile(
    fake_home: Path, config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(fake_home))
    result = RUNNER.invoke(APP, ["config", "init", "--config", str(config_path)], input="\n")
    assert result.exit_code == 0, result.output
    assert "YouTube Turk" in result.output
    expected = fake_home / ".mozilla" / "firefox" / "abc.Profile 1"
    assert load_config(config_path).auth.firefox_profile == expected


def test_init_warns_for_signed_out_profile(fake_home: Path, config_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(fake_home))
    result = RUNNER.invoke(APP, ["config", "init", "--config", str(config_path)], input="5\n2\n")
    assert result.exit_code == 0, result.output
    assert "Enter a number from 1 to 3" in result.output
    assert "not signed in to YouTube" in result.output
    assert load_config(config_path).auth.firefox_profile == fake_home / ".mozilla" / "firefox" / "def.default"


def test_init_refuses_to_overwrite(saved_config: AppConfig, config_path: Path) -> None:
    result = RUNNER.invoke(APP, ["config", "init", "--config", str(config_path)])
    assert result.exit_code == 1
    assert "already exists" in result.output


def test_init_without_firefox(tmp_path: Path, config_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    result = RUNNER.invoke(APP, ["config", "init", "--config", str(config_path)])
    assert result.exit_code == 1
    assert "No Firefox profiles found" in result.output
    assert not config_path.exists()


def test_commands_need_a_config(config_path: Path) -> None:
    result = RUNNER.invoke(APP, ["config", "show", "--config", str(config_path)])
    assert result.exit_code == 1
    assert "config init" in result.output


def test_add_channel(saved_config: AppConfig, config_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str | None] = []

    def fake_resolve(value: str, ytdlp: YtDlp) -> ChannelConfig:
        seen.append(ytdlp.cookies_spec)
        return CHANNEL.model_copy()

    monkeypatch.setattr(cli, "resolve_channel", fake_resolve)
    result = RUNNER.invoke(
        APP, ["config", "add-channel", "@3blue1brown", "--members", "--config", str(config_path)], input="y\n"
    )
    assert result.exit_code == 0, result.output
    assert seen == ["firefox:/p/abc.Profile 1"]
    channels = load_config(config_path).channels
    assert channels == [CHANNEL.model_copy(update={"members": True})]

    result = RUNNER.invoke(APP, ["config", "add-channel", "@3blue1brown", "-y", "--config", str(config_path)])
    assert result.exit_code == 1
    assert "already in the configuration" in result.output


def test_add_channel_declined(saved_config: AppConfig, config_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "resolve_channel", lambda value, ytdlp: CHANNEL.model_copy())
    result = RUNNER.invoke(APP, ["config", "add-channel", "@3blue1brown", "--config", str(config_path)], input="n\n")
    assert result.exit_code == 0
    assert load_config(config_path).channels == []


def test_add_channel_bad_url(saved_config: AppConfig, config_path: Path) -> None:
    result = RUNNER.invoke(APP, ["config", "add-channel", "not-a-channel", "--config", str(config_path)])
    assert result.exit_code == 1
    assert "Not a YouTube channel" in result.output


def test_remove_channel(config_path: Path) -> None:
    save_config(AppConfig(channels=[CHANNEL]), config_path)
    result = RUNNER.invoke(APP, ["config", "remove-channel", "3blue1brown", "--config", str(config_path)])
    assert result.exit_code == 0, result.output
    assert load_config(config_path).channels == []

    result = RUNNER.invoke(APP, ["config", "remove-channel", "3blue1brown", "--config", str(config_path)])
    assert result.exit_code == 1


def test_show(fake_home: Path, config_path: Path) -> None:
    config = AppConfig(channels=[CHANNEL])
    config.auth.firefox_profile = fake_home / ".mozilla" / "firefox" / "abc.Profile 1"
    save_config(config, config_path)
    result = RUNNER.invoke(APP, ["config", "show", "--config", str(config_path)])
    assert result.exit_code == 0, result.output
    assert 'channel_id = "UCYO"' in result.output
    assert "has YouTube sign-in cookies" in result.output


@pytest.mark.parametrize(("accepted", "exit_code"), [(True, 0), (False, 1)])
def test_check(
    saved_config: AppConfig, config_path: Path, monkeypatch: pytest.MonkeyPatch, accepted: bool, exit_code: int
) -> None:
    monkeypatch.setattr(cli, "verify_login", lambda ytdlp: accepted)
    result = RUNNER.invoke(APP, ["config", "check", "--config", str(config_path)])
    assert result.exit_code == exit_code, result.output
