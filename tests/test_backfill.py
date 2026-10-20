from pathlib import Path

import pytest
from typer.testing import CliRunner

from ces_youtube_tools import cli
from ces_youtube_tools.cli import APP
from ces_youtube_tools.video import VideoInfo
from ces_youtube_tools.ytdlp import YtDlp, YtDlpError
from ces_youtube_tools.config import AppConfig, save_config
from ces_youtube_tools.backfill import PendingGuide, BackfillError, plan_backfill, write_front_matter
from ces_youtube_tools.pipeline import CacheError
from ces_youtube_tools.front_matter import split_front_matter

GUIDE = "# Watching Guide: A talk\r\n\r\n[0:05](https://www.youtube.com/watch?v=abcdefghijk&t=5s): Hello.\r\n"
RUNNER = CliRunner()
VIDEO = VideoInfo(id="abcdefghijk", title="A talk", channel="A channel", upload_date="20261008", duration=60)


class FakeLoader:
    def __init__(self, error: Exception | None = None) -> None:
        self.error: Exception | None = error
        self.calls: list[str] = []

    def __call__(self, video_id: str) -> VideoInfo:
        self.calls.append(video_id)
        if self.error is not None:
            raise self.error
        return VIDEO.model_copy(update={"id": video_id})


@pytest.fixture
def guides(tmp_path: Path) -> Path:
    (tmp_path / "new.md").write_bytes(GUIDE.encode("utf-8"))
    (tmp_path / "done.md").write_text("---\nvideo_id: abcdefghijk\n---\n\n# Done\n", encoding="utf-8")
    (tmp_path / "broken.md").write_text("---\nnot: [valid\n---\n# Broken\n", encoding="utf-8")
    return tmp_path


def test_plan_backfill_skips_guides_with_front_matter(guides: Path) -> None:
    loader = FakeLoader()
    plan = plan_backfill([guides / "new.md", guides / "done.md", guides / "broken.md"], loader)
    assert plan.pending == [PendingGuide(path=guides / "new.md", content=GUIDE.encode("utf-8"), video=VIDEO)]
    assert plan.skipped == [guides / "done.md", guides / "broken.md"]
    assert loader.calls == ["abcdefghijk"]


@pytest.mark.parametrize("error", [YtDlpError("ERROR: [youtube] Join this channel"), CacheError("Cannot write")])
def test_plan_backfill_reports_every_problem(guides: Path, error: Exception) -> None:
    (guides / "nolinks.md").write_text("# No links\n", encoding="utf-8")
    (guides / "binary.md").write_bytes(b"\xff\xfe")
    paths = [guides / "new.md", guides / "nolinks.md", guides / "binary.md", guides / "done.md"]
    with pytest.raises(BackfillError) as info:
        _ = plan_backfill(paths, FakeLoader(error))
    message = str(info.value)
    assert message.startswith("3 of 4 guides cannot be backfilled, so none was changed:\n")
    assert f"new.md: cannot load the metadata of video abcdefghijk ({error})" in message
    assert "nolinks.md: no YouTube video link found" in message
    assert "binary.md: cannot read the guide" in message
    assert (guides / "new.md").read_bytes() == GUIDE.encode("utf-8")


def test_write_front_matter_keeps_the_guide_byte_for_byte(guides: Path) -> None:
    path = guides / "new.md"
    path.chmod(0o640)
    write_front_matter(PendingGuide(path=path, content=path.read_bytes(), video=VIDEO))
    content = path.read_bytes()
    assert content.endswith(b"\n\n" + GUIDE.encode("utf-8"))
    metadata, _ = split_front_matter(content.decode("utf-8"))
    assert (metadata.video_id, metadata.title, metadata.generated) == ("abcdefghijk", "A talk", None)
    assert "generated: null\n" in content.decode("utf-8")
    assert path.stat().st_mode & 0o777 == 0o640
    assert sorted(child.name for child in guides.iterdir()) == ["broken.md", "done.md", "new.md"]


def test_write_front_matter_through_a_symlink(guides: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    target = tmp_path_factory.mktemp("elsewhere") / "real.md"
    target.write_bytes(GUIDE.encode("utf-8"))
    link = guides / "link.md"
    link.symlink_to(target)
    write_front_matter(PendingGuide(path=link, content=link.read_bytes(), video=VIDEO))
    assert link.is_symlink()
    assert split_front_matter(target.read_text(encoding="utf-8"))[0].video_id == "abcdefghijk"
    assert target.read_bytes().endswith(b"\n\n" + GUIDE.encode("utf-8"))
    assert sorted(child.name for child in target.parent.iterdir()) == ["real.md"]


def test_write_front_matter_failure_leaves_the_guide_unchanged(guides: Path) -> None:
    path = guides / "new.md"
    (guides / ".new.md.tmp").mkdir()  # the temporary file cannot be written
    with pytest.raises(BackfillError, match=r"new\.md: cannot write the guide"):
        write_front_matter(PendingGuide(path=path, content=path.read_bytes(), video=VIDEO))
    assert path.read_bytes() == GUIDE.encode("utf-8")


@pytest.fixture
def cli_setup(guides: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, FakeLoader]:
    """A saved config, the guides folder as the working directory, and a fake in place of `load_video_info`."""
    config_path = guides / "config" / "config.toml"
    save_config(AppConfig(cache_dir=guides / "cache"), config_path)
    loader = FakeLoader()

    def fake_load_video_info(video_id: str, ytdlp: YtDlp, work_dir: Path, refresh: bool = False) -> VideoInfo:
        assert work_dir == guides / "cache" / video_id
        return loader(video_id)

    monkeypatch.setattr(cli, "load_video_info", fake_load_video_info)
    monkeypatch.chdir(guides)
    return config_path, loader


def test_backfill_command_writes_then_skips(cli_setup: tuple[Path, FakeLoader], guides: Path) -> None:
    config_path, loader = cli_setup
    result = RUNNER.invoke(APP, ["backfill", ".", "--config", str(config_path)])
    assert result.exit_code == 0, result.output
    assert f"Wrote {guides / 'new.md'}" in result.output
    assert "1 written, 2 skipped (already have front matter)." in result.output
    assert split_front_matter((guides / "new.md").read_text(encoding="utf-8"))[0].video_id == "abcdefghijk"

    result = RUNNER.invoke(APP, ["backfill", ".", "--config", str(config_path)])
    assert result.exit_code == 0, result.output
    assert "0 written, 3 skipped" in result.output
    assert loader.calls == ["abcdefghijk"]


def test_backfill_command_dry_run_changes_nothing(cli_setup: tuple[Path, FakeLoader], guides: Path) -> None:
    config_path, loader = cli_setup
    result = RUNNER.invoke(APP, ["backfill", "new.md", "--dry-run", "--config", str(config_path)])
    assert result.exit_code == 0, result.output
    assert f"Would write {guides / 'new.md'}" in result.output
    assert "1 to write, 0 skipped" in result.output
    assert loader.calls == ["abcdefghijk"]
    assert (guides / "new.md").read_bytes() == GUIDE.encode("utf-8")


def test_backfill_command_fails_without_changing_any_guide(cli_setup: tuple[Path, FakeLoader], guides: Path) -> None:
    config_path, loader = cli_setup
    loader.error = YtDlpError("ERROR: [youtube] abcdefghijk: Join this channel")
    (guides / "other.md").write_text("[0:01](https://www.youtube.com/watch?v=zzzzzzzzzzz&t=1s)\n", encoding="utf-8")
    result = RUNNER.invoke(APP, ["backfill", ".", "--config", str(config_path)], env={"COLUMNS": "300"})
    assert result.exit_code == 1
    assert "2 of 4 guides cannot be backfilled, so none was changed" in result.output
    assert "[youtube] abcdefghijk: Join this channel" in result.output
    assert (guides / "new.md").read_bytes() == GUIDE.encode("utf-8")
    assert not (guides / "other.md").read_text(encoding="utf-8").startswith("---")


def test_backfill_command_missing_path(cli_setup: tuple[Path, FakeLoader]) -> None:
    config_path, _ = cli_setup
    result = RUNNER.invoke(APP, ["backfill", "missing.md", "--config", str(config_path)])
    assert result.exit_code == 1
    assert "No such file or folder: 'missing.md'" in result.output
