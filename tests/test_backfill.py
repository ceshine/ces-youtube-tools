from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from typer.testing import CliRunner

from ces_youtube_tools import cli
from ces_youtube_tools.cli import APP
from ces_youtube_tools.video import VideoInfo
from ces_youtube_tools.ytdlp import YtDlp, YtDlpError
from ces_youtube_tools.config import AppConfig, GuideConfig, save_config
from ces_youtube_tools.backfill import (
    GuideUpdate,
    rename_guide,
    BackfillError,
    plan_backfill,
    write_content,
    describe_changes,
)
from ces_youtube_tools.pipeline import CacheError
from ces_youtube_tools.front_matter import read_front_matter

TAIPEI = ZoneInfo("Asia/Taipei")
GUIDE = "# Watching Guide: A talk\r\n\r\n[0:05](https://www.youtube.com/watch?v=abcdefghijk&t=5s): Hello.\r\n"
OTHER_GUIDE = "# Another talk\n\n[0:01](https://www.youtube.com/watch?v=zzzzzzzzzzz&t=1s): Hi.\n"
RUNNER = CliRunner()
# 2026-10-08T16:30:00Z, which is 2026-10-09 in Asia/Taipei.
VIDEO = VideoInfo(
    id="abcdefghijk", title="A talk", channel="A channel", upload_date="20261008", duration=60, timestamp=1791477000
)
NEW_NAME = "Watching Guide - 20261009 - A talk.md"


class FakeLoader:
    def __init__(self, failing: dict[str, Exception] | None = None) -> None:
        self.failing: dict[str, Exception] = failing or {}
        self.calls: list[str] = []

    def __call__(self, video_id: str) -> VideoInfo:
        self.calls.append(video_id)
        if video_id in self.failing:
            raise self.failing[video_id]
        if video_id == VIDEO.id:
            return VIDEO
        return VIDEO.model_copy(update={"id": video_id, "title": f"Talk {video_id}", "timestamp": None})


def front_matter(path: Path) -> dict[object, object] | None:
    return read_front_matter(path.read_bytes().decode("utf-8")).data


def write_all(paths: list[Path], loader: FakeLoader | None = None) -> None:
    for update in plan_backfill(paths, loader or FakeLoader(), TAIPEI).updates:
        write_content(update)
        rename_guide(update)


@pytest.fixture
def guides(tmp_path: Path) -> Path:
    folder = tmp_path / "guides"
    folder.mkdir()
    (folder / "new.md").write_bytes(GUIDE.encode("utf-8"))
    return folder


def test_a_guide_without_front_matter_gets_it_and_its_new_name(guides: Path) -> None:
    path = guides / "new.md"
    path.chmod(0o640)
    plan = plan_backfill([path], FakeLoader(), TAIPEI)
    assert (plan.unchanged, plan.problems) == ([], [])
    [update] = plan.updates
    assert (update.new_path, update.had_front_matter, describe_changes(update)) == (
        guides / NEW_NAME,
        False,
        "front matter added",
    )
    write_content(update)
    rename_guide(update)
    new_path = guides / NEW_NAME
    assert sorted(child.name for child in guides.iterdir()) == [NEW_NAME]
    assert new_path.read_bytes().endswith(b"---\n\n" + GUIDE.encode("utf-8"))
    data = front_matter(new_path)
    assert data is not None
    assert (data["video_id"], data["published"], data["generated"]) == (
        "abcdefghijk",
        "2026-10-08T16:30:00Z",
        "2026-10-08T16:30:00Z",  # filled from the publish time
    )
    assert new_path.stat().st_mode & 0o777 == 0o640


def test_front_matter_is_rewritten_keeping_generated_and_unknown_keys(guides: Path) -> None:
    path = guides / NEW_NAME
    path.write_text(
        "---\ntags: [ai]\nvideo_id: abcdefghijk\ntitle: Old title\ngenerated: 2026-10-10\n---\n\n# Guide\n",
        encoding="utf-8",
    )
    [update] = plan_backfill([path], FakeLoader(), TAIPEI).updates
    assert update.new_path == path
    # `members_only` is added as null: a key missing from the block counts as changed.
    assert update.changed_keys == [
        "title",
        "channel",
        "channel_id",
        "upload_date",
        "published",
        "duration",
        "members_only",
    ]
    assert describe_changes(update) == "title, channel, channel_id, upload_date, published, duration, members_only"
    write_content(update)
    rename_guide(update)
    text = path.read_text(encoding="utf-8")
    assert text.endswith("generated: 2026-10-10\ntags:\n- ai\n---\n\n# Guide\n")
    data = front_matter(path)
    assert data is not None
    assert list(data)[-2:] == ["generated", "tags"]
    assert data["title"] == "A talk"


def test_generated_stays_unknown_without_a_publish_time(guides: Path) -> None:
    path = guides / "other.md"
    path.write_text(OTHER_GUIDE, encoding="utf-8")
    write_all([path])
    data = front_matter(guides / "Watching Guide - 20261008 - Talk zzzzzzzzzzz.md")
    assert data is not None
    assert (data["published"], data["generated"]) == (None, None)


def test_a_second_run_changes_nothing(guides: Path) -> None:
    (guides / "other.md").write_text(OTHER_GUIDE, encoding="utf-8")
    write_all([guides / "new.md", guides / "other.md"])
    paths = sorted(guides.iterdir())
    contents = [path.read_bytes() for path in paths]
    plan = plan_backfill(paths, FakeLoader(), TAIPEI)
    assert (plan.updates, plan.unchanged, plan.problems) == ([], paths, [])
    assert [path.read_bytes() for path in paths] == contents


def test_only_the_layout_of_the_block_changes(guides: Path) -> None:
    path = guides / "new.md"
    write_all([path])
    new_path = guides / NEW_NAME
    # Unquoted times and a comment: the same values, written differently.
    text = new_path.read_text(encoding="utf-8").replace("'2026-10-08T16:30:00Z'", "2026-10-08T16:30:00Z  # set")
    new_path.write_text(text, encoding="utf-8")
    [update] = plan_backfill([new_path], FakeLoader(), TAIPEI).updates
    assert (update.changed_keys, describe_changes(update)) == ([], "front matter reformatted, no value changed")


def test_a_rename_alone_keeps_the_content(guides: Path) -> None:
    write_all([guides / "new.md"])
    old_path = guides / "old name.md"
    (guides / NEW_NAME).rename(old_path)
    content = old_path.read_bytes()
    [update] = plan_backfill([old_path], FakeLoader(), TAIPEI).updates
    assert (update.content, describe_changes(update)) == (None, None)
    write_content(update)
    rename_guide(update)
    assert sorted(child.name for child in guides.iterdir()) == [NEW_NAME]
    assert (guides / NEW_NAME).read_bytes() == content


def test_guides_with_problems_of_their_own_are_listed_and_the_others_kept(guides: Path) -> None:
    (guides / "nolinks.md").write_text("# No links\n", encoding="utf-8")
    (guides / "binary.md").write_bytes(b"\xff\xfe")
    (guides / "broken.md").write_text("---\nnot: [valid\n---\n# Broken\n", encoding="utf-8")
    (guides / "badid.md").write_text("---\nvideo_id: ../../etc\n---\n# Bad\n", encoding="utf-8")
    (guides / "other.md").write_text(OTHER_GUIDE, encoding="utf-8")
    paths = sorted(guides.iterdir())
    loader = FakeLoader({"zzzzzzzzzzz": YtDlpError("ERROR: [youtube] zzzzzzzzzzz: Join this channel")})
    plan = plan_backfill(paths, loader, TAIPEI)
    assert [update.path for update in plan.updates] == [guides / "new.md"]
    expected = [
        ("badid.md", "the front matter's `video_id` is not a YouTube video ID: '../../etc'"),
        ("binary.md", "cannot read the guide ('utf-8' codec can't decode byte 0xff"),
        ("broken.md", "the front matter is not valid YAML at line 3"),
        ("nolinks.md", "no `video_id` in the front matter and no YouTube video link"),
        ("other.md", "cannot load the metadata of video zzzzzzzzzzz (ERROR: [youtube] zzzzzzzzzzz: Join this channel)"),
    ]
    assert len(plan.problems) == len(expected)
    for problem, (name, start) in zip(plan.problems, expected, strict=True):
        assert problem.startswith(f"{guides / name}: {start}")
    assert loader.calls == ["abcdefghijk", "zzzzzzzzzzz"]


def test_two_guides_for_one_video_stop_the_run_before_loading(guides: Path) -> None:
    (guides / "copy.md").write_bytes(GUIDE.encode("utf-8"))
    (guides / "nolinks.md").write_text("# No links\n", encoding="utf-8")
    loader = FakeLoader()
    with pytest.raises(BackfillError) as info:
        _ = plan_backfill(sorted(guides.iterdir()), loader, TAIPEI)
    assert str(info.value) == (
        "No guide was changed, because of problems between guides:\n"
        f"- video abcdefghijk has 2 guides: {guides / 'copy.md'}, {guides / 'new.md'}\n"
        "These guides also have problems of their own:\n"
        f"- {guides / 'nolinks.md'}: no `video_id` in the front matter and no YouTube video link"
    )
    assert loader.calls == []


def test_a_taken_new_name_stops_the_run(guides: Path) -> None:
    (guides / NEW_NAME).write_text("# Notes, not a guide\n", encoding="utf-8")
    with pytest.raises(BackfillError, match=r"new\.md: its new name is taken by .*20261009 - A talk\.md$"):
        _ = plan_backfill([guides / "new.md"], FakeLoader(), TAIPEI)


def test_two_guides_with_one_new_name_stop_the_run(guides: Path) -> None:
    (guides / "other.md").write_text(OTHER_GUIDE, encoding="utf-8")
    same_title = VIDEO.model_copy(update={"id": "zzzzzzzzzzz"})

    def load(video_id: str) -> VideoInfo:
        return VIDEO if video_id == VIDEO.id else same_title

    with pytest.raises(BackfillError, match=rf"other\.md: its new name {NEW_NAME} is also the new name of .*new\.md"):
        _ = plan_backfill([guides / "new.md", guides / "other.md"], load, TAIPEI)


def test_a_symlinked_guide_is_left_as_it_is(guides: Path) -> None:
    # The link's new name is the name of the file it points to: renaming the link would replace that file.
    target = guides / NEW_NAME
    (guides / "new.md").rename(target)
    link = guides / "link.md"
    link.symlink_to(NEW_NAME)
    plan = plan_backfill([link], FakeLoader(), TAIPEI)
    assert plan.updates == []
    assert plan.problems == [
        f"{link}: the guide is a symbolic link; replace it with the file it points to, then run again"
    ]
    assert link.is_symlink() and target.read_bytes() == GUIDE.encode("utf-8")


def test_blank_lines_at_the_top_are_dropped_whatever_their_line_ending(guides: Path) -> None:
    path = guides / "new.md"
    path.write_bytes(b"\r\n\n" + GUIDE.encode("utf-8"))
    write_all([path])
    assert (guides / NEW_NAME).read_bytes().endswith(b"---\n\n" + GUIDE.encode("utf-8"))


def test_a_failed_write_leaves_the_guide_unchanged(guides: Path) -> None:
    path = guides / "new.md"
    (guides / ".new.md.tmp").mkdir()  # the temporary file cannot be written
    [update] = plan_backfill([path], FakeLoader(), TAIPEI).updates
    with pytest.raises(BackfillError, match=r"new\.md: cannot write the guide"):
        write_content(update)
    assert path.read_bytes() == GUIDE.encode("utf-8")


def test_a_name_taken_after_the_check_is_not_replaced(guides: Path) -> None:
    path = guides / "new.md"
    [update] = plan_backfill([path], FakeLoader(), TAIPEI).updates
    write_content(update)
    (guides / NEW_NAME).write_text("# Notes\n", encoding="utf-8")
    with pytest.raises(BackfillError, match=r"new\.md: cannot rename it, because its new name is now taken"):
        rename_guide(update)
    assert (guides / NEW_NAME).read_text(encoding="utf-8") == "# Notes\n"
    assert front_matter(path) is not None


@pytest.fixture
def cli_setup(guides: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, FakeLoader, list[bool]]:
    """A saved config, the guides folder as the working directory, and a fake in place of `load_video_info`."""
    config_path = guides.parent / "config" / "config.toml"
    save_config(
        AppConfig(cache_dir=guides.parent / "cache", guide=GuideConfig(file_name_timezone="Asia/Taipei")), config_path
    )
    loader = FakeLoader()
    refreshes: list[bool] = []

    def fake_load_video_info(video_id: str, ytdlp: YtDlp, work_dir: Path, refresh: bool = False) -> VideoInfo:
        assert work_dir == guides.parent / "cache" / video_id
        refreshes.append(refresh)
        return loader(video_id)

    monkeypatch.setattr(cli, "load_video_info", fake_load_video_info)
    monkeypatch.chdir(guides)
    return config_path, loader, refreshes


def test_backfill_command_writes_renames_then_changes_nothing(
    cli_setup: tuple[Path, FakeLoader, list[bool]], guides: Path
) -> None:
    config_path, _, refreshes = cli_setup
    result = RUNNER.invoke(APP, ["backfill", ".", "--config", str(config_path)], env={"COLUMNS": "300"})
    assert result.exit_code == 0, result.output
    assert f"Wrote {guides / 'new.md'}: front matter added" in result.output
    assert f"Renamed {guides / 'new.md'} to {NEW_NAME}" in result.output
    assert "1 written, 1 renamed, 0 unchanged, 0 with problems." in result.output
    assert sorted(child.name for child in guides.iterdir()) == [NEW_NAME]

    result = RUNNER.invoke(APP, ["backfill", ".", "--refresh", "--config", str(config_path)])
    assert result.exit_code == 0, result.output
    assert "0 written, 0 renamed, 1 unchanged, 0 with problems." in result.output
    assert refreshes == [False, True]


def test_backfill_command_dry_run_changes_nothing(cli_setup: tuple[Path, FakeLoader, list[bool]], guides: Path) -> None:
    config_path, loader, _ = cli_setup
    result = RUNNER.invoke(
        APP, ["backfill", "new.md", "--dry-run", "--config", str(config_path)], env={"COLUMNS": "300"}
    )
    assert result.exit_code == 0, result.output
    assert f"Would write {guides / 'new.md'}: front matter added" in result.output
    assert f"Would rename {guides / 'new.md'} to {NEW_NAME}" in result.output
    assert "1 to write, 1 to rename, 0 unchanged, 0 with problems." in result.output
    assert loader.calls == ["abcdefghijk"]
    assert sorted(child.name for child in guides.iterdir()) == ["new.md"]
    assert (guides / "new.md").read_bytes() == GUIDE.encode("utf-8")


def test_backfill_command_writes_the_others_when_one_video_fails(
    cli_setup: tuple[Path, FakeLoader, list[bool]], guides: Path
) -> None:
    config_path, loader, _ = cli_setup
    loader.failing = {"zzzzzzzzzzz": YtDlpError("ERROR: [youtube] zzzzzzzzzzz: Join this channel")}
    (guides / "other.md").write_text(OTHER_GUIDE, encoding="utf-8")
    result = RUNNER.invoke(APP, ["backfill", ".", "--config", str(config_path)], env={"COLUMNS": "300"})
    assert result.exit_code == 1
    assert "1 written, 1 renamed, 0 unchanged, 1 with problems." in result.output
    assert "1 of 2 guides have problems:" in result.output
    assert "[youtube] zzzzzzzzzzz: Join this channel" in result.output
    assert sorted(child.name for child in guides.iterdir()) == [NEW_NAME, "other.md"]
    assert (guides / "other.md").read_text(encoding="utf-8") == OTHER_GUIDE


def test_backfill_command_reports_a_write_whose_rename_fails(
    cli_setup: tuple[Path, FakeLoader, list[bool]], guides: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path, _, _ = cli_setup

    def failing_rename(update: GuideUpdate) -> None:
        raise BackfillError(f"{update.path}: cannot rename it to {update.new_path.name} (Permission denied)")

    monkeypatch.setattr(cli, "rename_guide", failing_rename)
    result = RUNNER.invoke(APP, ["backfill", ".", "--config", str(config_path)], env={"COLUMNS": "300"})
    assert result.exit_code == 1
    assert f"Wrote {guides / 'new.md'}: front matter added" in result.output
    assert "Renamed" not in result.output
    assert "1 written, 0 renamed, 0 unchanged, 1 with problems." in result.output
    assert "1 of 1 guides have problems:" in result.output
    assert front_matter(guides / "new.md") is not None


def test_backfill_command_stops_on_two_guides_for_one_video(
    cli_setup: tuple[Path, FakeLoader, list[bool]], guides: Path
) -> None:
    config_path, _, _ = cli_setup
    (guides / "copy.md").write_bytes(GUIDE.encode("utf-8"))
    result = RUNNER.invoke(APP, ["backfill", ".", "--config", str(config_path)], env={"COLUMNS": "300"})
    assert result.exit_code == 1
    assert "No guide was changed, because of problems between guides" in result.output
    assert sorted(child.name for child in guides.iterdir()) == ["copy.md", "new.md"]


def test_backfill_command_missing_path(cli_setup: tuple[Path, FakeLoader, list[bool]]) -> None:
    config_path, _, _ = cli_setup
    result = RUNNER.invoke(APP, ["backfill", "missing.md", "--config", str(config_path)])
    assert result.exit_code == 1
    assert "No such file or folder: 'missing.md'" in result.output


@pytest.mark.parametrize("error", [YtDlpError("ERROR: [youtube] Join this channel"), CacheError("Cannot write")])
def test_both_loader_errors_are_problems_of_one_guide(guides: Path, error: Exception) -> None:
    plan = plan_backfill([guides / "new.md"], FakeLoader({"abcdefghijk": error}), TAIPEI)
    assert (plan.updates, plan.problems) == (
        [],
        [f"{guides / 'new.md'}: cannot load the metadata of video abcdefghijk ({error})"],
    )
