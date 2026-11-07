from pathlib import Path

import pytest
from typer.testing import CliRunner

from ces_youtube_tools.cli import APP, config_cmd
from ces_youtube_tools.ytdlp import YtDlp
from ces_youtube_tools.config import AppConfig, load_config, save_config, ChannelConfig

from .conftest import CHANNEL

RUNNER = CliRunner()


@pytest.fixture
def saved_config(config_path: Path) -> AppConfig:
    config = AppConfig()
    config.auth.firefox_profile = Path("/p/abc.Profile 1")
    save_config(config, config_path)
    return config


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


def test_init_writes_template_next_to_config(
    fake_home: Path, config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(fake_home))
    result = RUNNER.invoke(APP, ["config", "init", "--config", str(config_path)], input="\n")
    assert result.exit_code == 0, result.output
    template = config_path.parent / "guide_prompt.md"
    assert load_config(config_path).guide.prompt_template == template
    assert "$transcript" in template.read_text(encoding="utf-8")


def test_config_template(config_path: Path, tmp_path: Path) -> None:
    config = AppConfig()
    config.guide.prompt_template = tmp_path / "templates" / "guide_prompt.md"
    save_config(config, config_path)
    result = RUNNER.invoke(APP, ["config", "template", "--config", str(config_path)])
    assert result.exit_code == 0, result.output
    assert "$transcript" in config.guide.prompt_template.read_text(encoding="utf-8")

    config.guide.prompt_template.write_text("edited", encoding="utf-8")
    result = RUNNER.invoke(APP, ["config", "template", "--config", str(config_path)])
    assert result.exit_code == 1
    assert config.guide.prompt_template.read_text(encoding="utf-8") == "edited"
    result = RUNNER.invoke(APP, ["config", "template", "--force", "--config", str(config_path)])
    assert result.exit_code == 0
    assert "$transcript" in config.guide.prompt_template.read_text(encoding="utf-8")


def test_add_channel(saved_config: AppConfig, config_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str | None] = []

    def fake_resolve(value: str, ytdlp: YtDlp) -> ChannelConfig:
        seen.append(ytdlp.cookies_spec)
        return CHANNEL.model_copy()

    monkeypatch.setattr(config_cmd, "resolve_channel", fake_resolve)
    result = RUNNER.invoke(
        APP, ["config", "add-channel", "@3blue1brown", "--members", "--config", str(config_path)], input="y\n"
    )
    assert result.exit_code == 0, result.output
    assert seen == [None]  # channel lookups never send cookies
    channels = load_config(config_path).channels
    assert channels == [CHANNEL.model_copy(update={"members": True})]

    result = RUNNER.invoke(APP, ["config", "add-channel", "@3blue1brown", "-y", "--config", str(config_path)])
    assert result.exit_code == 1
    assert "already in the configuration" in result.output


def test_add_channel_declined(saved_config: AppConfig, config_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_cmd, "resolve_channel", lambda value, ytdlp: CHANNEL.model_copy())
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
    assert "Guides are written by Claude Code with claude-sonnet-5-5 (high effort)." in result.output


@pytest.mark.parametrize(
    ("generator", "options", "expected"),
    [
        (
            "agy-cli",
            {"model": "gemini-3.8-flash-high"},
            "Guides are written by Antigravity CLI with gemini-3.8-flash-high.",
        ),
        ("agy-cli", {"model": "gemini-3.8-flash"}, "Invalid settings in [guide.options.agy-cli]"),
        ("pi-cli", {}, "Guides are written by Pi with openai/gpt-6.1-terra (high effort)."),
        ("pi-cli", {"model": "terra"}, "Invalid settings in [guide.options.pi-cli]"),
        ("no-such-generator", {}, "Unknown guide generator 'no-such-generator'"),
    ],
)
def test_show_checks_generator(config_path: Path, generator: str, options: dict[str, object], expected: str) -> None:
    config = AppConfig()
    config.guide.generator = generator
    config.guide.options[generator] = options
    save_config(config, config_path)
    result = RUNNER.invoke(APP, ["config", "show", "--config", str(config_path)], terminal_width=200)
    assert result.exit_code == 0, result.output
    assert expected in result.output


def test_options_needs_no_config() -> None:
    result = RUNNER.invoke(APP, ["config", "options"], terminal_width=200)
    assert result.exit_code == 0, result.output
    assert "guide.output_dir" in result.output
    assert "guide.options.claude-cli.model" in result.output
    assert "guide.options.agy-cli.model" in result.output
    assert "guide.options.pi-cli.model" in result.output


@pytest.mark.parametrize(("accepted", "exit_code"), [(True, 0), (False, 1)])
def test_check(
    saved_config: AppConfig, config_path: Path, monkeypatch: pytest.MonkeyPatch, accepted: bool, exit_code: int
) -> None:
    monkeypatch.setattr(config_cmd, "verify_login", lambda ytdlp: accepted)
    result = RUNNER.invoke(APP, ["config", "check", "--config", str(config_path)])
    assert result.exit_code == exit_code, result.output
