from pathlib import Path

import pytest
from typer.testing import CliRunner

from ces_youtube_tools import cli
from ces_youtube_tools.cli import APP
from ces_youtube_tools.video import VideoInfo
from ces_youtube_tools.ytdlp import YtDlp, YtDlpError
from ces_youtube_tools.config import AppConfig, load_config, save_config, ChannelConfig
from ces_youtube_tools.youtube import VideoSummary
from ces_youtube_tools.transcript import Transcript, TranscriptError, TranscriptSegment, TranscriptProvider
from ces_youtube_tools.front_matter import split_front_matter

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


def test_options_needs_no_config() -> None:
    result = RUNNER.invoke(APP, ["config", "options"], terminal_width=200)
    assert result.exit_code == 0, result.output
    assert "guide.output_dir" in result.output
    assert "guide.options.claude-cli.model" in result.output


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


def test_transcript_unusable_cache_dir_exits_1(config_path: Path, tmp_path: Path) -> None:
    config = AppConfig(cache_dir=tmp_path / "cache")
    config.auth.firefox_profile = Path("/p/abc.Profile 1")
    save_config(config, config_path)
    (tmp_path / "cache").write_text("not a directory", encoding="utf-8")
    result = RUNNER.invoke(APP, ["transcript", "3i2C3IB3PaA", "--config", str(config_path)], env={"COLUMNS": "1000"})
    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "Error: Cannot create the work directory" in result.output


def test_transcript_bad_video(config_path: Path) -> None:
    save_config(AppConfig(), config_path)
    result = RUNNER.invoke(APP, ["transcript", "https://example.com/x", "--config", str(config_path)])
    assert result.exit_code == 1
    assert "Not a YouTube video" in result.output


def guide_body(path: Path) -> str:
    return split_front_matter(path.read_text(encoding="utf-8"))[1]


GUIDE_REPLY = "# Watching Guide: A video\n\n[0:05](https://www.youtube.com/watch?v=3i2C3IB3PaA&t=5s)\n"


class FakeGuideGenerator:
    def __init__(self, reply: str = GUIDE_REPLY) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    def generate(self, prompt: str, video: VideoInfo, work_dir: Path) -> str:
        self.prompts.append(prompt)
        return self.reply


@pytest.fixture
def guide_setup(config_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeGuideGenerator:
    config = AppConfig(cache_dir=tmp_path / "cache")
    config.guide.prompt_template = tmp_path / "missing_template.md"
    save_config(config, config_path)
    generator = FakeGuideGenerator()

    def fake_info(video_id: str, ytdlp: YtDlp, work_dir: Path, refresh: bool = False) -> VideoInfo:
        return VideoInfo(id=video_id, title="A video", duration=60, upload_date="20261008")

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
    assert guide_body(workdir / "Watching Guide - 20261008 - A video.md") == GUIDE_REPLY
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
    assert (tmp_path / "from-config" / "Watching Guide - 20261008 - A video.md").exists()
    result = RUNNER.invoke(
        APP, ["guide", "3i2C3IB3PaA", "-o", str(tmp_path / "from-option"), "--config", str(config_path)]
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "from-option" / "Watching Guide - 20261008 - A video.md").exists()


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


def test_help_text_stops_at_docstring_sections() -> None:
    def documented() -> None:
        """Do a thing.

        More detail.

        Args:
            x (int): Hidden from help.
        """

    assert cli.help_text(documented) == "Do a thing.\n\nMore detail."
    result = RUNNER.invoke(APP, ["list", "--help"], env={"COLUMNS": "200"})
    assert "Args:" not in result.output
    assert "newest first" in result.output


TYT = ChannelConfig(
    name="The Young Turks", url="https://www.youtube.com/@TheYoungTurks", channel_id="UCtyt", members=True
)
LISTED = [
    VideoSummary(id="aaaaaaaaaaa", title="First video", duration=300),
    VideoSummary(id="3i2C3IB3PaA", title="Bonus episode", duration=815, members_only=True),
]
LISTED_TITLES = {video.id: video.title for video in LISTED}


class FakePickers:
    """Stands in for the arrow-key menus: returns set answers and records each menu."""

    def __init__(self) -> None:
        self.channel: int | None = 1
        self.videos: list[int] | None = [1]
        self.menus: list[tuple[str, list[str]]] = []

    def pick_one(self, message: str, options: list[str], default: int | None = None) -> int | None:
        self.menus.append((message, options))
        return self.channel

    def pick_many(self, message: str, options: list[str]) -> list[int] | None:
        self.menus.append((message, options))
        return self.videos


@pytest.fixture
def pickers(monkeypatch: pytest.MonkeyPatch) -> FakePickers:
    fake = FakePickers()
    monkeypatch.setattr(cli, "require_terminal", lambda: None)
    monkeypatch.setattr(cli, "pick_one", fake.pick_one)
    monkeypatch.setattr(cli, "pick_many", fake.pick_many)
    return fake


@pytest.fixture
def browse_setup(
    guide_setup: FakeGuideGenerator, pickers: FakePickers, config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> list[str]:
    config = load_config(config_path)
    config.channels = [CHANNEL, TYT]
    save_config(config, config_path)
    listed: list[str] = []

    def fake_list(channel: ChannelConfig, ytdlp: YtDlp, limit: int) -> list[VideoSummary]:
        listed.append(channel.name)
        assert ytdlp.cookies_spec is None  # listing never sends cookies
        return LISTED

    monkeypatch.setattr(cli, "list_latest_videos", fake_list)
    return listed


@pytest.fixture
def titled(browse_setup: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """Give each listed video its own title, so their guides get different file names."""

    def fake_info(video_id: str, ytdlp: YtDlp, work_dir: Path, refresh: bool = False) -> VideoInfo:
        return VideoInfo(id=video_id, title=LISTED_TITLES[video_id], duration=900, upload_date="20261008")

    monkeypatch.setattr(cli, "load_video_info", fake_info)


def summary_results(output: str) -> dict[str, str]:
    """Read the result of each video from the summary table of `browse`, by title."""
    results: dict[str, str] = {}
    for line in output.splitlines():
        cells = [cell.strip() for cell in line.split("│")[1:-1]]
        if len(cells) == 3 and cells[1] in ("written", "kept", "failed"):
            results[cells[0]] = cells[1]
    return results


def test_browse_menus_then_guide(
    browse_setup: list[str], pickers: FakePickers, guide_setup: FakeGuideGenerator, config_path: Path, tmp_path: Path
) -> None:
    pickers.channel = 2
    pickers.videos = [2]
    args = ["browse", "-o", str(tmp_path / "out"), "--config", str(config_path)]
    result = RUNNER.invoke(APP, args, env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    assert browse_setup == ["The Young Turks"]
    (channel_message, channel_labels), (video_message, video_labels) = pickers.menus
    assert channel_message == "Channel"
    assert channel_labels == ["3Blue1Brown", "The Young Turks (members-only listed)"]
    assert video_message.startswith("Latest videos of The Young Turks")
    assert video_labels[0].endswith("First video")
    assert video_labels[1].endswith("Bonus episode")
    assert "members" in video_labels[1]
    assert "[1/1] Bonus episode (https://www.youtube.com/watch?v=3i2C3IB3PaA)" in result.output
    assert (tmp_path / "out" / "Watching Guide - 20261008 - A video.md").exists()
    assert len(guide_setup.prompts) == 1
    assert summary_results(result.output) == {}  # no summary table for a single video


def test_browse_channel_argument_and_overwrite_prompt(
    browse_setup: list[str], guide_setup: FakeGuideGenerator, config_path: Path, tmp_path: Path
) -> None:
    existing = tmp_path / "Watching Guide - 20261008 - A video.md"
    existing.write_text("mine", encoding="utf-8")
    args = ["browse", "3blue1brown", "-o", str(tmp_path), "--config", str(config_path)]
    result = RUNNER.invoke(APP, args, input="n\n")
    assert result.exit_code == 0, result.output
    assert browse_setup == ["3Blue1Brown"]
    assert "Kept the existing guide." in result.output
    assert existing.read_text(encoding="utf-8") == "mine"
    assert guide_setup.prompts == []

    result = RUNNER.invoke(APP, args, input="y\n")
    assert result.exit_code == 0, result.output
    assert guide_body(existing) == GUIDE_REPLY


@pytest.mark.parametrize("menu", ["channel", "videos"])
def test_browse_quit(
    browse_setup: list[str], pickers: FakePickers, guide_setup: FakeGuideGenerator, config_path: Path, menu: str
) -> None:
    setattr(pickers, menu, None)  # what a picker returns on Ctrl+C
    result = RUNNER.invoke(APP, ["browse", "--config", str(config_path)])
    assert result.exit_code == 0, result.output
    assert "Bye." in result.output
    assert guide_setup.prompts == []


def test_browse_single_channel_skips_the_channel_menu(
    browse_setup: list[str], pickers: FakePickers, config_path: Path, tmp_path: Path
) -> None:
    config = load_config(config_path)
    config.channels = [TYT]
    save_config(config, config_path)
    result = RUNNER.invoke(APP, ["browse", "-o", str(tmp_path), "--config", str(config_path)])
    assert result.exit_code == 0, result.output
    assert [message for message, _ in pickers.menus] == [
        "Latest videos of The Young Turks (Space selects, Enter confirms)"
    ]
    assert browse_setup == ["The Young Turks"]


def test_browse_errors(config_path: Path) -> None:
    save_config(AppConfig(), config_path)
    result = RUNNER.invoke(APP, ["browse", "--config", str(config_path)])
    assert result.exit_code == 1
    assert "No channels configured" in result.output

    save_config(AppConfig(channels=[CHANNEL]), config_path)
    result = RUNNER.invoke(APP, ["browse", "nope", "--config", str(config_path)], env={"COLUMNS": "200"})
    assert result.exit_code == 1
    assert "Configured channels: 3Blue1Brown" in result.output


def test_browse_needs_a_terminal(config_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    save_config(AppConfig(channels=[CHANNEL]), config_path)

    def no_listing(channel: ChannelConfig, ytdlp: YtDlp, limit: int) -> list[VideoSummary]:
        raise AssertionError("the videos must not be listed")

    monkeypatch.setattr(cli, "list_latest_videos", no_listing)
    result = RUNNER.invoke(APP, ["browse", "--config", str(config_path)], env={"COLUMNS": "200"})
    assert result.exit_code == 1
    assert "Interactive menus need a terminal. For scripts, use `ces-youtube-tools guide VIDEO`." in result.output


def test_browse_writes_a_guide_for_each_selected_video(
    titled: None, pickers: FakePickers, guide_setup: FakeGuideGenerator, config_path: Path, tmp_path: Path
) -> None:
    pickers.videos = [1, 2]
    args = ["browse", "3blue1brown", "-o", str(tmp_path), "--config", str(config_path)]
    result = RUNNER.invoke(APP, args, env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    assert result.output.index("[1/2] First video") < result.output.index("[2/2] Bonus episode")
    assert len(guide_setup.prompts) == 2
    assert guide_body(tmp_path / "Watching Guide - 20261008 - First video.md") == GUIDE_REPLY
    assert guide_body(tmp_path / "Watching Guide - 20261008 - Bonus episode.md") == GUIDE_REPLY
    assert summary_results(result.output) == {"First video": "written", "Bonus episode": "written"}


def test_browse_asks_every_question_before_writing(
    titled: None, pickers: FakePickers, guide_setup: FakeGuideGenerator, config_path: Path, tmp_path: Path
) -> None:
    existing = tmp_path / "Watching Guide - 20261008 - Bonus episode.md"
    existing.write_text("mine", encoding="utf-8")
    pickers.videos = [1, 2]
    args = ["browse", "3blue1brown", "-o", str(tmp_path), "--config", str(config_path)]
    result = RUNNER.invoke(APP, args, input="n\n", env={"COLUMNS": "1000"})
    assert result.exit_code == 0, result.output
    assert result.output.index(f"{existing} already exists. Replace it?") < result.output.index("[1/1] First video")
    assert len(guide_setup.prompts) == 1
    assert existing.read_text(encoding="utf-8") == "mine"
    assert (tmp_path / "Watching Guide - 20261008 - First video.md").exists()
    assert summary_results(result.output) == {"First video": "written", "Bonus episode": "kept"}


def test_browse_carries_on_after_a_failure(
    titled: None,
    pickers: FakePickers,
    guide_setup: FakeGuideGenerator,
    config_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load_transcript = cli.load_transcript  # the fake from `guide_setup`

    def failing_transcript(
        video: VideoInfo, provider: TranscriptProvider, work_dir: Path, refresh: bool = False
    ) -> Transcript:
        if video.id == "aaaaaaaaaaa":
            raise TranscriptError("No subtitles for aaaaaaaaaaa.")
        return load_transcript(video, provider, work_dir, refresh=refresh)

    monkeypatch.setattr(cli, "load_transcript", failing_transcript)
    pickers.videos = [1, 2]
    args = ["browse", "3blue1brown", "-o", str(tmp_path), "--config", str(config_path)]
    result = RUNNER.invoke(APP, args, env={"COLUMNS": "200"})
    assert result.exit_code == 1
    assert "Error: No subtitles for aaaaaaaaaaa." in result.output
    assert "[2/2] Bonus episode" in result.output
    assert (tmp_path / "Watching Guide - 20261008 - Bonus episode.md").exists()
    assert not (tmp_path / "Watching Guide - 20261008 - First video.md").exists()
    assert summary_results(result.output) == {"First video": "failed", "Bonus episode": "written"}
    assert "Error: 1 of 2 guides failed." in result.output


def test_browse_carries_on_when_a_video_cannot_be_loaded(
    titled: None,
    pickers: FakePickers,
    guide_setup: FakeGuideGenerator,
    config_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load_video_info = cli.load_video_info  # the fake from `titled`

    def members_only(video_id: str, ytdlp: YtDlp, work_dir: Path, refresh: bool = False) -> VideoInfo:
        if video_id == "3i2C3IB3PaA":
            raise YtDlpError(f"ERROR: [youtube] {video_id}: Join this channel to get access to members-only content")
        return load_video_info(video_id, ytdlp, work_dir, refresh=refresh)

    monkeypatch.setattr(cli, "load_video_info", members_only)
    pickers.videos = [1, 2]
    args = ["browse", "3blue1brown", "-o", str(tmp_path), "--config", str(config_path)]
    result = RUNNER.invoke(APP, args, env={"COLUMNS": "200"})
    assert result.exit_code == 1
    assert "Error: Bonus episode (3i2C3IB3PaA): ERROR: [youtube] 3i2C3IB3PaA: Join this channel" in result.output
    assert "[1/1] First video" in result.output
    assert len(guide_setup.prompts) == 1
    assert "Error: 1 of 2 guides failed." in result.output


def test_browse_refuses_two_videos_with_the_same_guide_file(
    browse_setup: list[str], pickers: FakePickers, guide_setup: FakeGuideGenerator, config_path: Path, tmp_path: Path
) -> None:
    # `guide_setup` gives every video the same title and upload date, so both guides get the same file name.
    pickers.videos = [1, 2]
    args = ["browse", "3blue1brown", "-o", str(tmp_path), "--config", str(config_path)]
    result = RUNNER.invoke(APP, args, env={"COLUMNS": "1000"})
    assert result.exit_code == 1
    target = tmp_path / "Watching Guide - 20261008 - A video.md"
    assert (
        f"Error: Bonus episode (3i2C3IB3PaA): {target} is also the guide file, or an older guide, of First video "
        "(aaaaaaaaaaa)." in result.output
    )
    assert "`ces-youtube-tools guide 3i2C3IB3PaA -o DIR`" in result.output
    assert len(guide_setup.prompts) == 1
    assert "[1/1] First video" in result.output
    assert "Error: 1 of 2 guides failed." in result.output


def test_browse_refuses_a_video_whose_older_guide_another_video_overwrites(
    titled: None, pickers: FakePickers, guide_setup: FakeGuideGenerator, config_path: Path, tmp_path: Path
) -> None:
    # An older guide of the second video sits where the first video's guide goes. Writing the second guide would
    # remove it as an older guide, taking the first video's new guide with it.
    shared = tmp_path / "Watching Guide - 20261008 - First video.md"
    shared.write_text(OLD_GUIDE, encoding="utf-8")
    pickers.videos = [1, 2]
    args = ["browse", "3blue1brown", "-o", str(tmp_path), "--config", str(config_path)]
    result = RUNNER.invoke(APP, args, input="y\n", env={"COLUMNS": "1000"})
    assert result.exit_code == 1
    assert result.output.count("Replace it?") == 1  # the refused video is not asked
    assert f"{shared} is also the guide file, or an older guide, of First video (aaaaaaaaaaa)." in result.output
    assert len(guide_setup.prompts) == 1
    assert guide_body(shared) == GUIDE_REPLY
    assert summary_results(result.output) == {"First video": "written", "Bonus episode": "failed"}


def test_guide_write_failure_exits_1_with_a_message(
    guide_setup: FakeGuideGenerator, config_path: Path, tmp_path: Path
) -> None:
    (tmp_path / "Watching Guide - 20261008 - A video.md").mkdir()
    args = ["guide", "3i2C3IB3PaA", "-o", str(tmp_path), "--force", "--config", str(config_path)]
    result = RUNNER.invoke(APP, args, env={"COLUMNS": "1000"})
    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "Error: Cannot write" in result.output
    assert "raw reply is kept in" in result.output


def test_guide_unusable_cache_dir_exits_1(guide_setup: FakeGuideGenerator, config_path: Path, tmp_path: Path) -> None:
    (tmp_path / "cache").write_text("not a directory", encoding="utf-8")
    args = ["guide", "3i2C3IB3PaA", "-o", str(tmp_path / "out"), "--config", str(config_path)]
    result = RUNNER.invoke(APP, args, env={"COLUMNS": "1000"})
    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "Error: Cannot create the work directory" in result.output
    assert guide_setup.prompts == []


def test_guide_warns_when_the_raw_reply_is_not_saved(
    guide_setup: FakeGuideGenerator, config_path: Path, tmp_path: Path
) -> None:
    (tmp_path / "cache" / "3i2C3IB3PaA" / "guide.md").mkdir(parents=True)
    args = ["guide", "3i2C3IB3PaA", "-o", str(tmp_path / "out"), "--config", str(config_path)]
    result = RUNNER.invoke(APP, args, env={"COLUMNS": "1000"})
    assert result.exit_code == 0, result.output
    assert "Warning: the raw reply was not kept in the cache: Cannot write the cache file" in result.output
    assert guide_body(tmp_path / "out" / "Watching Guide - 20261008 - A video.md") == GUIDE_REPLY


def test_guide_error_shows_square_brackets_in_the_title(
    guide_setup: FakeGuideGenerator, config_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def bracket_info(video_id: str, ytdlp: YtDlp, work_dir: Path, refresh: bool = False) -> VideoInfo:
        return VideoInfo(id=video_id, title="[/x] [bold]Live[/bold]", duration=60, upload_date="20261008")

    monkeypatch.setattr(cli, "load_video_info", bracket_info)
    (tmp_path / "Watching Guide - 20261008 - [ x] [bold]Live[ bold].md").write_text("mine", encoding="utf-8")
    args = ["guide", "3i2C3IB3PaA", "-o", str(tmp_path), "--config", str(config_path)]
    result = RUNNER.invoke(APP, args, env={"COLUMNS": "1000"})
    assert result.exit_code == 1
    assert "[ x] [bold]Live[ bold].md already exists" in result.output


def test_guide_error_keeps_the_ytdlp_extractor_tag(
    guide_setup: FakeGuideGenerator, config_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_info(video_id: str, ytdlp: YtDlp, work_dir: Path, refresh: bool = False) -> VideoInfo:
        raise YtDlpError(f"ERROR: [youtube] {video_id}: Join this channel to get access to members-only content")

    monkeypatch.setattr(cli, "load_video_info", failing_info)
    args = ["guide", "3i2C3IB3PaA", "-o", str(tmp_path), "--config", str(config_path)]
    result = RUNNER.invoke(APP, args, env={"COLUMNS": "1000"})
    assert result.exit_code == 1
    assert "ERROR: [youtube] 3i2C3IB3PaA: Join this channel" in result.output


def test_guide_without_upload_date_leaves_the_date_out_of_the_file_name(
    guide_setup: FakeGuideGenerator, config_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def undated_info(video_id: str, ytdlp: YtDlp, work_dir: Path, refresh: bool = False) -> VideoInfo:
        return VideoInfo(id=video_id, title="A video", duration=60)

    monkeypatch.setattr(cli, "load_video_info", undated_info)
    args = ["guide", "3i2C3IB3PaA", "-o", str(tmp_path), "--config", str(config_path)]
    result = RUNNER.invoke(APP, args)
    assert result.exit_code == 0, result.output
    assert guide_body(tmp_path / "Watching Guide - A video.md") == GUIDE_REPLY


OLD_LINKS = "# Old guide\n\n[0:05](https://www.youtube.com/watch?v=3i2C3IB3PaA&t=5s)\n"
OLD_GUIDE = "---\nvideo_id: 3i2C3IB3PaA\n---\n\n" + OLD_LINKS


def test_guide_refuses_a_guide_for_the_video_under_another_name(
    guide_setup: FakeGuideGenerator, config_path: Path, tmp_path: Path
) -> None:
    old = tmp_path / "Watching Guide - 20261007 - A video.md"
    old.write_text(OLD_GUIDE, encoding="utf-8")
    (tmp_path / "unreadable.md").write_bytes(b"\xff\xfe")
    args = ["guide", "3i2C3IB3PaA", "-o", str(tmp_path), "--config", str(config_path)]
    result = RUNNER.invoke(APP, args, env={"COLUMNS": "1000"})
    assert result.exit_code == 1
    assert f"Video 3i2C3IB3PaA already has a guide: {old}. Use --force to replace it." in result.output
    assert f"Warning: Cannot read {tmp_path / 'unreadable.md'}" in result.output
    assert guide_setup.prompts == []

    result = RUNNER.invoke(APP, [*args, "--force"], env={"COLUMNS": "1000"})
    assert result.exit_code == 0, result.output
    assert f"Removed the older guide {old}" in result.output
    assert not old.exists()
    assert guide_body(tmp_path / "Watching Guide - 20261008 - A video.md") == GUIDE_REPLY


def test_browse_asks_before_replacing_a_guide_under_another_name(
    browse_setup: list[str], pickers: FakePickers, guide_setup: FakeGuideGenerator, config_path: Path, tmp_path: Path
) -> None:
    old = tmp_path / "Watching Guide - 20261007 - A video.md"
    old.write_text(OLD_GUIDE, encoding="utf-8")
    pickers.videos = [2]
    args = ["browse", "3blue1brown", "-o", str(tmp_path), "--config", str(config_path)]
    result = RUNNER.invoke(APP, args, input="n\n", env={"COLUMNS": "1000"})
    assert result.exit_code == 0, result.output
    assert f"{old} already exists. Replace it?" in result.output
    assert "Kept the existing guide." in result.output
    assert guide_setup.prompts == []

    result = RUNNER.invoke(APP, args, input="y\n", env={"COLUMNS": "1000"})
    assert result.exit_code == 0, result.output
    assert not old.exists()
    assert guide_body(tmp_path / "Watching Guide - 20261008 - A video.md") == GUIDE_REPLY


def test_guide_never_replaces_a_file_found_only_by_its_links(
    guide_setup: FakeGuideGenerator, config_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    notes = tmp_path / "My notes.md"
    notes.write_text(OLD_LINKS, encoding="utf-8")
    fetched: list[str] = []

    def no_transcript(video: VideoInfo, provider: object, work_dir: Path, refresh: bool = False) -> Transcript:
        fetched.append(video.id)
        raise AssertionError("the transcript must not be fetched")

    monkeypatch.setattr(cli, "load_transcript", no_transcript)
    args = ["guide", "3i2C3IB3PaA", "-o", str(tmp_path), "--force", "--config", str(config_path)]
    result = RUNNER.invoke(APP, args, env={"COLUMNS": "1000"})
    assert result.exit_code == 1
    assert f"{notes} links to this video, but its front matter does not name it" in result.output
    assert fetched == []
    assert notes.read_text(encoding="utf-8") == OLD_LINKS
