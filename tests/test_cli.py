from pathlib import Path

import pytest
from rich.cells import cell_len
from typer.testing import CliRunner

from ces_youtube_tools import cli, pipeline
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


def test_help_runs() -> None:
    result = RUNNER.invoke(APP, ["--help"])
    assert result.exit_code == 0
    assert "watching guides" in result.output


def test_commands_need_a_config(config_path: Path) -> None:
    result = RUNNER.invoke(APP, ["config", "show", "--config", str(config_path)])
    assert result.exit_code == 1
    assert "config init" in result.output


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

    monkeypatch.setattr(pipeline, "load_video_info", fake_info)
    monkeypatch.setattr(pipeline, "load_transcript", fake_transcript)
    monkeypatch.setattr(pipeline, "make_guide_generator", lambda config: generator)
    return generator


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
OLDER = [
    VideoSummary(id="bbbbbbbbbbb", title="Older video", duration=600),
    VideoSummary(id="ccccccccccc", title="Oldest video", duration=900),
]
LISTED_TITLES = {video.id: video.title for video in [*LISTED, *OLDER]}


class FakePickers:
    """Stands in for the arrow-key menus: returns set answers and records each menu.

    `videos` holds the answers to the video menus, one per menu in order; each is used once.
    """

    def __init__(self) -> None:
        self.channel: int | None = 1
        self.videos: list[list[int] | None] = [[1]]
        self.menus: list[tuple[str, list[str]]] = []
        self.checked: list[set[int]] = []
        self.required: list[bool] = []

    def pick_one(self, message: str, options: list[str], default: int | None = None) -> int | None:
        self.menus.append((message, options))
        return self.channel

    def pick_many(
        self, message: str, options: list[str], checked: set[int] | None = None, require_selection: bool = True
    ) -> list[int] | None:
        self.menus.append((message, options))
        self.checked.append(checked or set())
        self.required.append(require_selection)
        if not self.videos:
            raise AssertionError(f"No answer left for the menu {message!r}")
        return self.videos.pop(0)


class FakePager:
    """Stands in for `VideoPager`: each new page is the next of the set fetch results, a page of videos or an error.

    An error (a `YtDlpError`, or `KeyboardInterrupt` for Ctrl+C while loading) is raised once, so the retry gets the
    result after it. `has_more` is True while results are left.
    """

    def __init__(self, fetches: list[list[VideoSummary] | BaseException]) -> None:
        self.fetches = list(fetches)
        self.pages: list[list[VideoSummary]] = []

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def has_more(self) -> bool:
        return bool(self.fetches)

    def fetched(self) -> list[VideoSummary]:
        return [video for page in self.pages for video in page]

    def page(self, number: int) -> list[VideoSummary]:
        if number <= len(self.pages):
            return self.pages[number - 1]
        assert number == len(self.pages) + 1, f"page {number} skips a page"
        result = self.fetches.pop(0)
        if isinstance(result, BaseException):
            raise result
        if result:
            self.pages.append(result)
        return result


@pytest.fixture
def pickers(monkeypatch: pytest.MonkeyPatch) -> FakePickers:
    fake = FakePickers()
    monkeypatch.setattr(cli, "require_terminal", lambda: None)
    monkeypatch.setattr(cli, "pick_one", fake.pick_one)
    monkeypatch.setattr(cli, "pick_many", fake.pick_many)
    return fake


@pytest.fixture
def pages() -> list[list[VideoSummary] | BaseException]:
    """The listing's fetch results for `browse`, in order: a page of videos, or an error. Tests may replace them."""
    return [LISTED]


@pytest.fixture
def browse_setup(
    guide_setup: FakeGuideGenerator,
    pickers: FakePickers,
    pages: list[list[VideoSummary] | BaseException],
    config_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> list[str]:
    config = load_config(config_path)
    config.channels = [CHANNEL, TYT]
    save_config(config, config_path)
    listed: list[str] = []

    def fake_pager(channel: ChannelConfig, ytdlp: YtDlp, page_size: int) -> FakePager:
        listed.append(channel.name)
        assert ytdlp.cookies_spec is None  # listing never sends cookies
        assert page_size == 20
        return FakePager(pages)

    monkeypatch.setattr(cli, "VideoPager", fake_pager)
    return listed


@pytest.fixture
def titled(browse_setup: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """Give each listed video its own title, so their guides get different file names."""

    def fake_info(video_id: str, ytdlp: YtDlp, work_dir: Path, refresh: bool = False) -> VideoInfo:
        return VideoInfo(id=video_id, title=LISTED_TITLES[video_id], duration=900, upload_date="20261008")

    monkeypatch.setattr(pipeline, "load_video_info", fake_info)


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
    pickers.videos = [[2]]
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
    assert len(video_labels) == 2  # one page only: no page entries
    assert "members" in video_labels[1]
    assert "[1/1] Bonus episode (https://www.youtube.com/watch?v=3i2C3IB3PaA)" in result.output
    assert (tmp_path / "out" / "Watching Guide - 20261008 - A video.md").exists()
    assert len(guide_setup.prompts) == 1
    assert summary_results(result.output) == {}  # no summary table for a single video


def test_browse_channel_argument_and_overwrite_prompt(
    browse_setup: list[str], pickers: FakePickers, guide_setup: FakeGuideGenerator, config_path: Path, tmp_path: Path
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

    pickers.videos = [[1]]
    result = RUNNER.invoke(APP, args, input="y\n")
    assert result.exit_code == 0, result.output
    assert guide_body(existing) == GUIDE_REPLY


@pytest.mark.parametrize("menu", ["channel", "videos"])
def test_browse_quit(
    browse_setup: list[str], pickers: FakePickers, guide_setup: FakeGuideGenerator, config_path: Path, menu: str
) -> None:
    # None is what a picker returns on Ctrl+C.
    if menu == "channel":
        pickers.channel = None
    else:
        pickers.videos = [None]
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


def test_browse_video_labels_fit_beside_the_checkbox(
    browse_setup: list[str], pickers: FakePickers, pages: list[list[VideoSummary] | BaseException], config_path: Path
) -> None:
    pages[:] = [[VideoSummary(id="aaaaaaaaaaa", title="x" * 200, duration=300)]]
    pickers.videos = [None]  # quit at the video menu
    result = RUNNER.invoke(APP, ["browse", "3blue1brown", "--config", str(config_path)], env={"COLUMNS": "80"})
    assert result.exit_code == 0, result.output
    ((_, labels),) = pickers.menus
    # questionary's checkbox draws 5 cells before each label (see docs/implementation_notes.md), so a cut label
    # fills the rest of the line and no more.
    assert cell_len(labels[0]) == 80 - 5


def test_browse_picks_across_pages_newest_first(
    titled: None,
    pickers: FakePickers,
    pages: list[list[VideoSummary] | BaseException],
    guide_setup: FakeGuideGenerator,
    config_path: Path,
    tmp_path: Path,
) -> None:
    pages[:] = [LISTED, OLDER]
    # Page 1: Next page. Page 2: "Older video" together with Previous page. Page 1: "Bonus episode", confirm.
    pickers.videos = [[3], [1, 3], [2]]
    args = ["browse", "3blue1brown", "-o", str(tmp_path), "--config", str(config_path)]
    result = RUNNER.invoke(APP, args, env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    (first, first_labels), (second, second_labels), (third, _) = pickers.menus
    assert first == "Latest videos of 3Blue1Brown (Space selects, Enter confirms)"
    assert first_labels[2:] == [cli._NEXT_PAGE_LABEL]  # no Previous page on page 1
    assert second == "Videos 3–4 of 3Blue1Brown (Space selects, Enter confirms)"
    assert second_labels[2:] == ["◂ Previous page (videos 1–2)"]  # no Next page: no more videos
    assert third == "Latest videos of 3Blue1Brown (1 selected on other pages) (Space selects, Enter confirms)"
    assert pickers.required == [True, True, False]
    # Newest first across pages, not in the order picked.
    assert result.output.index("[1/2] Bonus episode") < result.output.index("[2/2] Older video")
    assert summary_results(result.output) == {"Bonus episode": "written", "Older video": "written"}


def test_browse_shows_earlier_ticks_and_drops_unticked_videos(
    browse_setup: list[str],
    pickers: FakePickers,
    pages: list[list[VideoSummary] | BaseException],
    guide_setup: FakeGuideGenerator,
    config_path: Path,
    tmp_path: Path,
) -> None:
    pages[:] = [LISTED, OLDER]
    # Page 1: "First video" and Next page. Page 2: Previous page. Page 1: untick "First video", tick "Bonus episode".
    pickers.videos = [[1, 3], [3], [2]]
    args = ["browse", "3blue1brown", "-o", str(tmp_path), "--config", str(config_path)]
    result = RUNNER.invoke(APP, args, env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    assert pickers.checked == [set(), set(), {1}]
    assert pickers.required == [True, False, True]
    assert "[1/1] Bonus episode" in result.output
    assert len(guide_setup.prompts) == 1


def test_browse_both_page_entries_show_the_page_again(
    browse_setup: list[str],
    pickers: FakePickers,
    pages: list[list[VideoSummary] | BaseException],
    guide_setup: FakeGuideGenerator,
    config_path: Path,
    tmp_path: Path,
) -> None:
    pages[:] = [LISTED, OLDER, [VideoSummary(id="ddddddddddd", title="Ancient video")]]
    # Page 2: "Older video" with both page entries (as `a` would tick), then confirm.
    pickers.videos = [[3], [1, 3, 4], [1]]
    args = ["browse", "3blue1brown", "-o", str(tmp_path), "--config", str(config_path)]
    result = RUNNER.invoke(APP, args, env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    assert "Tick one page entry to change pages, or neither to confirm." in result.output
    assert pickers.menus[1] == pickers.menus[2]
    assert pickers.checked[2] == {1}
    assert "[1/1] Older video" in result.output


def test_browse_empty_later_page_shows_the_page_again_without_next(
    browse_setup: list[str],
    pickers: FakePickers,
    pages: list[list[VideoSummary] | BaseException],
    guide_setup: FakeGuideGenerator,
    config_path: Path,
    tmp_path: Path,
) -> None:
    pages[:] = [LISTED, []]
    pickers.videos = [[1, 3], [1]]
    args = ["browse", "3blue1brown", "-o", str(tmp_path), "--config", str(config_path)]
    result = RUNNER.invoke(APP, args, env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    assert "No older videos." in result.output
    assert len(pickers.menus[1][1]) == 2  # no Next page any more
    assert pickers.checked[1] == {1}
    assert "[1/1] First video" in result.output


def test_browse_later_page_error_keeps_the_selection(
    titled: None,
    pickers: FakePickers,
    pages: list[list[VideoSummary] | BaseException],
    guide_setup: FakeGuideGenerator,
    config_path: Path,
    tmp_path: Path,
) -> None:
    pages[:] = [LISTED, YtDlpError("ERROR: HTTP Error 500"), OLDER]
    # Next page fails, the retry works.
    pickers.videos = [[2, 3], [2, 3], [1]]
    args = ["browse", "3blue1brown", "-o", str(tmp_path), "--config", str(config_path)]
    result = RUNNER.invoke(APP, args, env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    assert "Error: ERROR: HTTP Error 500" in result.output
    assert pickers.menus[0] == pickers.menus[1]
    assert pickers.checked[1] == {2}
    assert summary_results(result.output) == {"Bonus episode": "written", "Older video": "written"}


@pytest.mark.parametrize("where", ["menu", "loading"])
def test_browse_quit_on_a_later_page(
    browse_setup: list[str],
    pickers: FakePickers,
    pages: list[list[VideoSummary] | BaseException],
    guide_setup: FakeGuideGenerator,
    config_path: Path,
    where: str,
) -> None:
    # Ctrl+C in page 2's menu, or while page 2 loads.
    if where == "menu":
        pages[:] = [LISTED, OLDER]
        pickers.videos = [[1, 3], None]
    else:
        pages[:] = [LISTED, KeyboardInterrupt()]
        pickers.videos = [[1, 3]]
    result = RUNNER.invoke(APP, ["browse", "3blue1brown", "--config", str(config_path)])
    assert result.exit_code == 0, result.output
    assert "Bye." in result.output
    assert guide_setup.prompts == []


@pytest.mark.parametrize(
    "first_page, exit_code, message",
    [
        ([], 0, "No videos found for 3Blue1Brown."),
        (YtDlpError("ERROR: HTTP Error 500"), 1, "HTTP Error 500"),
        (KeyboardInterrupt(), 0, "Bye."),
    ],
)
def test_browse_first_page_empty_or_failing(
    browse_setup: list[str],
    pickers: FakePickers,
    pages: list[list[VideoSummary] | BaseException],
    config_path: Path,
    first_page: list[VideoSummary] | BaseException,
    exit_code: int,
    message: str,
) -> None:
    pages[:] = [first_page]
    result = RUNNER.invoke(APP, ["browse", "3blue1brown", "--config", str(config_path)], env={"COLUMNS": "200"})
    assert result.exit_code == exit_code, result.output
    assert message in result.output
    assert pickers.menus == []


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

    def no_listing(channel: ChannelConfig, ytdlp: YtDlp, page_size: int) -> FakePager:
        raise AssertionError("the videos must not be listed")

    monkeypatch.setattr(cli, "VideoPager", no_listing)
    result = RUNNER.invoke(APP, ["browse", "--config", str(config_path)], env={"COLUMNS": "200"})
    assert result.exit_code == 1
    assert "Interactive menus need a terminal. For scripts, use `ces-youtube-tools guide VIDEO`." in result.output


def test_browse_writes_a_guide_for_each_selected_video(
    titled: None, pickers: FakePickers, guide_setup: FakeGuideGenerator, config_path: Path, tmp_path: Path
) -> None:
    pickers.videos = [[1, 2]]
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
    pickers.videos = [[1, 2]]
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
    load_transcript = pipeline.load_transcript  # the fake from `guide_setup`

    def failing_transcript(
        video: VideoInfo, provider: TranscriptProvider, work_dir: Path, refresh: bool = False
    ) -> Transcript:
        if video.id == "aaaaaaaaaaa":
            raise TranscriptError("No subtitles for aaaaaaaaaaa.")
        return load_transcript(video, provider, work_dir, refresh=refresh)

    monkeypatch.setattr(pipeline, "load_transcript", failing_transcript)
    pickers.videos = [[1, 2]]
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
    load_video_info = pipeline.load_video_info  # the fake from `titled`

    def members_only(video_id: str, ytdlp: YtDlp, work_dir: Path, refresh: bool = False) -> VideoInfo:
        if video_id == "3i2C3IB3PaA":
            raise YtDlpError(f"ERROR: [youtube] {video_id}: Join this channel to get access to members-only content")
        return load_video_info(video_id, ytdlp, work_dir, refresh=refresh)

    monkeypatch.setattr(pipeline, "load_video_info", members_only)
    pickers.videos = [[1, 2]]
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
    pickers.videos = [[1, 2]]
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
    pickers.videos = [[1, 2]]
    args = ["browse", "3blue1brown", "-o", str(tmp_path), "--config", str(config_path)]
    result = RUNNER.invoke(APP, args, input="y\n", env={"COLUMNS": "1000"})
    assert result.exit_code == 1
    assert result.output.count("Replace it?") == 1  # the refused video is not asked
    assert f"{shared} is also the guide file, or an older guide, of First video (aaaaaaaaaaa)." in result.output
    assert len(guide_setup.prompts) == 1
    assert guide_body(shared) == GUIDE_REPLY
    assert summary_results(result.output) == {"First video": "written", "Bonus episode": "failed"}


OLD_LINKS = "# Old guide\n\n[0:05](https://www.youtube.com/watch?v=3i2C3IB3PaA&t=5s)\n"
OLD_GUIDE = "---\nvideo_id: 3i2C3IB3PaA\n---\n\n" + OLD_LINKS


def test_browse_asks_before_replacing_a_guide_under_another_name(
    browse_setup: list[str], pickers: FakePickers, guide_setup: FakeGuideGenerator, config_path: Path, tmp_path: Path
) -> None:
    old = tmp_path / "Watching Guide - 20261007 - A video.md"
    old.write_text(OLD_GUIDE, encoding="utf-8")
    pickers.videos = [[2]]
    args = ["browse", "3blue1brown", "-o", str(tmp_path), "--config", str(config_path)]
    result = RUNNER.invoke(APP, args, input="n\n", env={"COLUMNS": "1000"})
    assert result.exit_code == 0, result.output
    assert f"{old} already exists. Replace it?" in result.output
    assert "Kept the existing guide." in result.output
    assert guide_setup.prompts == []

    pickers.videos = [[2]]
    result = RUNNER.invoke(APP, args, input="y\n", env={"COLUMNS": "1000"})
    assert result.exit_code == 0, result.output
    assert not old.exists()
    assert guide_body(tmp_path / "Watching Guide - 20261008 - A video.md") == GUIDE_REPLY
