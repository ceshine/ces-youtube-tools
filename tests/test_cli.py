from pathlib import Path

import pytest
from typer.testing import CliRunner

from youtube_tools import cli
from youtube_tools.cli import APP
from youtube_tools.video import VideoInfo
from youtube_tools.ytdlp import YtDlp
from youtube_tools.config import AppConfig, load_config, save_config, ChannelConfig
from youtube_tools.youtube import VideoSummary
from youtube_tools.transcript import Transcript, TranscriptSegment

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
    assert seen == [None]  # channel lookups never send cookies
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

    monkeypatch.setattr(cli, "list_latest_videos", fake_list)
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
    monkeypatch.setattr(cli, "list_latest_videos", lambda channel, ytdlp, limit: [])
    result = RUNNER.invoke(APP, ["list", "3blue1brown", "--config", str(config_path)])
    assert result.exit_code == 0
    assert "No videos found" in result.output


def test_transcript(config_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = AppConfig(cache_dir=tmp_path / "cache")
    config.auth.firefox_profile = Path("/p/abc.Profile 1")
    save_config(config, config_path)
    seen: dict[str, object] = {}

    def fake_info(video_id: str, ytdlp: YtDlp, work_dir: Path, refresh: bool = False) -> VideoInfo:
        seen.update(video_id=video_id, cookies=ytdlp.cookies_spec, work_dir=work_dir, refresh=refresh)
        return VideoInfo(id=video_id, title="A video", channel="A channel")

    def fake_transcript(video: VideoInfo, provider: object, work_dir: Path, refresh: bool = False) -> Transcript:
        return Transcript(
            video_id=video.id,
            source="yt-dlp-subs: automatic en-orig",
            language="en-orig",
            segments=[
                TranscriptSegment(start=3.6, end=5.0, text="All right."),
                TranscriptSegment(start=65, end=70, text="Next."),
            ],
        )

    monkeypatch.setattr(cli, "load_video_info", fake_info)
    monkeypatch.setattr(cli, "load_transcript", fake_transcript)
    url = "https://youtu.be/3i2C3IB3PaA"
    result = RUNNER.invoke(APP, ["transcript", url, "--refresh", "--config", str(config_path)])
    assert result.exit_code == 0, result.output
    assert seen == {
        "video_id": "3i2C3IB3PaA",
        "cookies": "firefox:/p/abc.Profile 1",  # members-only videos need cookies
        "work_dir": tmp_path / "cache" / "3i2C3IB3PaA",
        "refresh": True,
    }
    assert "[0:03] All right.\n[1:05] Next.\n" in result.stdout

    result = RUNNER.invoke(APP, ["transcript", url, "--json", "--config", str(config_path)])
    assert result.exit_code == 0, result.output
    assert Transcript.model_validate_json(result.stdout).segments[1].text == "Next."


def test_transcript_bad_video(config_path: Path) -> None:
    save_config(AppConfig(), config_path)
    result = RUNNER.invoke(APP, ["transcript", "https://example.com/x", "--config", str(config_path)])
    assert result.exit_code == 1
    assert "Not a YouTube video" in result.output


GUIDE_REPLY = "# Watching Guide: A video\n\n[0:05](https://www.youtube.com/watch?v=3i2C3IB3PaA&t=5s)\n"


class FakeGuideGenerator:
    def __init__(self, reply: str = GUIDE_REPLY) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    def generate(self, prompt: str, work_dir: Path) -> str:
        self.prompts.append(prompt)
        return self.reply


@pytest.fixture
def guide_setup(config_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeGuideGenerator:
    config = AppConfig(cache_dir=tmp_path / "cache")
    config.guide.prompt_template = tmp_path / "missing_template.md"
    save_config(config, config_path)
    generator = FakeGuideGenerator()

    def fake_info(video_id: str, ytdlp: YtDlp, work_dir: Path, refresh: bool = False) -> VideoInfo:
        return VideoInfo(id=video_id, title="A video", duration=60)

    def fake_transcript(video: VideoInfo, provider: object, work_dir: Path, refresh: bool = False) -> Transcript:
        segments = [TranscriptSegment(start=5, end=6, text="Hello.")]
        return Transcript(video_id=video.id, source="yt-dlp-subs: human-made en", language="en", segments=segments)

    monkeypatch.setattr(cli, "load_video_info", fake_info)
    monkeypatch.setattr(cli, "load_transcript", fake_transcript)
    monkeypatch.setattr(cli, "make_guide_generator", lambda config: generator)
    return generator


def test_guide_writes_to_the_current_directory_by_default(
    guide_setup: FakeGuideGenerator, config_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workdir = tmp_path / "here"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    result = RUNNER.invoke(APP, ["guide", "3i2C3IB3PaA", "--config", str(config_path)], env={"COLUMNS": "300"})
    assert result.exit_code == 0, result.output
    assert (workdir / "Watching Guide A video.md").read_text(encoding="utf-8") == GUIDE_REPLY
    assert "Using the built-in prompt template" in result.output
    assert "1 timestamp links, 0 labels corrected." in result.output
    assert "[0:05 | 5s] Hello." in guide_setup.prompts[0]

    result = RUNNER.invoke(APP, ["guide", "3i2C3IB3PaA", "--config", str(config_path)], env={"COLUMNS": "300"})
    assert result.exit_code == 1
    assert "already exists" in result.output
    assert len(guide_setup.prompts) == 1


def test_guide_output_dir_option_beats_config(
    guide_setup: FakeGuideGenerator, config_path: Path, tmp_path: Path
) -> None:
    config = load_config(config_path)
    config.guide.output_dir = tmp_path / "from-config"
    save_config(config, config_path)
    result = RUNNER.invoke(APP, ["guide", "3i2C3IB3PaA", "--config", str(config_path)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "from-config" / "Watching Guide A video.md").exists()
    result = RUNNER.invoke(
        APP, ["guide", "3i2C3IB3PaA", "-o", str(tmp_path / "from-option"), "--config", str(config_path)]
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "from-option" / "Watching Guide A video.md").exists()


def test_guide_warns_about_bad_links(guide_setup: FakeGuideGenerator, config_path: Path, tmp_path: Path) -> None:
    guide_setup.reply = "# Guide\n\n[9:00](https://www.youtube.com/watch?v=3i2C3IB3PaA&t=540s)\n"
    result = RUNNER.invoke(APP, ["guide", "3i2C3IB3PaA", "-o", str(tmp_path), "--config", str(config_path)])
    assert result.exit_code == 0, result.output
    assert "links past the end of the video: 9:00" in result.output

    guide_setup.reply = "# Guide without links\n"
    result = RUNNER.invoke(APP, ["guide", "3i2C3IB3PaA", "-o", str(tmp_path), "--force", "--config", str(config_path)])
    assert "no timestamp links" in result.output


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


def test_init_writes_template_next_to_config(
    fake_home: Path, config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(fake_home))
    result = RUNNER.invoke(APP, ["config", "init", "--config", str(config_path)], input="\n")
    assert result.exit_code == 0, result.output
    template = config_path.parent / "guide_prompt.md"
    assert load_config(config_path).guide.prompt_template == template
    assert "$transcript" in template.read_text(encoding="utf-8")
