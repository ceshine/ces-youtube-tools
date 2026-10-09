from pathlib import Path

import pytest

from youtube_tools.config import (
    AppConfig,
    ConfigError,
    load_config,
    save_config,
    ChannelConfig,
    DEFAULT_YTDLP_COMMAND,
)


def test_defaults() -> None:
    config = AppConfig()
    assert config.ytdlp_command == DEFAULT_YTDLP_COMMAND
    assert config.auth.firefox_profile is None
    assert config.transcript.provider == "yt-dlp-subs"
    assert config.guide.generator == "claude-cli"
    assert config.guide.output_dir is None


def test_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "config.toml"
    config = AppConfig()
    config.auth.firefox_profile = Path("/p/abc.Profile 1")
    config.channels.append(ChannelConfig(name="A", url="https://www.youtube.com/@a", channel_id="UCa", members=True))
    config.guide.options["claude-cli"] = {"model": "opus"}
    save_config(config, path)
    assert load_config(path) == config


def test_unset_optional_values_are_omitted(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    save_config(AppConfig(), path)
    text = path.read_text(encoding="utf-8")
    assert "firefox_profile" not in text
    assert "output_dir" not in text


def test_paths_expand_home(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[guide]\noutput_dir = "~/guides"\n', encoding="utf-8")
    assert load_config(path).guide.output_dir == Path.home() / "guides"


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="config init"):
        _ = load_config(tmp_path / "nope.toml")


def test_invalid_toml(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("not = [valid", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid TOML"):
        _ = load_config(path)


def test_invalid_values(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[[channels]]\nname = "A"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid"):
        _ = load_config(path)


def test_find_channel() -> None:
    channel = ChannelConfig(name="3Blue1Brown", url="u", channel_id="UCx")
    config = AppConfig(channels=[channel])
    assert config.find_channel("3blue1brown") is channel
    assert config.find_channel("UCx") is channel
    assert config.find_channel("other") is None
