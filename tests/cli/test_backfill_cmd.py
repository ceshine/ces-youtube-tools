from pathlib import Path

import pytest
from typer.testing import CliRunner

from ces_youtube_tools.cli import APP, backfill_cmd
from ces_youtube_tools.video import VideoInfo
from ces_youtube_tools.ytdlp import YtDlp, YtDlpError
from ces_youtube_tools.config import AppConfig, GuideConfig, save_config
from ces_youtube_tools.backfill import GuideUpdate, BackfillError

from ..conftest import (
    FakeLoader,
    front_matter,
    BACKFILL_GUIDE,
    BACKFILL_NEW_NAME,
    BACKFILL_OTHER_GUIDE,
    write_backfill_guides,
)

RUNNER = CliRunner()


@pytest.fixture
def guides(tmp_path: Path) -> Path:
    return write_backfill_guides(tmp_path)


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

    monkeypatch.setattr(backfill_cmd, "load_video_info", fake_load_video_info)
    monkeypatch.chdir(guides)
    return config_path, loader, refreshes


def test_backfill_command_writes_renames_then_changes_nothing(
    cli_setup: tuple[Path, FakeLoader, list[bool]], guides: Path
) -> None:
    config_path, _, refreshes = cli_setup
    result = RUNNER.invoke(APP, ["backfill", ".", "--config", str(config_path)], env={"COLUMNS": "300"})
    assert result.exit_code == 0, result.output
    assert f"Wrote {guides / 'new.md'}: front matter added" in result.output
    assert f"Renamed {guides / 'new.md'} to {BACKFILL_NEW_NAME}" in result.output
    assert "1 written, 1 renamed, 0 unchanged, 0 with problems." in result.output
    assert sorted(child.name for child in guides.iterdir()) == [BACKFILL_NEW_NAME]

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
    assert f"Would rename {guides / 'new.md'} to {BACKFILL_NEW_NAME}" in result.output
    assert "1 to write, 1 to rename, 0 unchanged, 0 with problems." in result.output
    assert loader.calls == ["abcdefghijk"]
    assert sorted(child.name for child in guides.iterdir()) == ["new.md"]
    assert (guides / "new.md").read_bytes() == BACKFILL_GUIDE.encode("utf-8")


def test_backfill_command_writes_the_others_when_one_video_fails(
    cli_setup: tuple[Path, FakeLoader, list[bool]], guides: Path
) -> None:
    config_path, loader, _ = cli_setup
    loader.failing = {"zzzzzzzzzzz": YtDlpError("ERROR: [youtube] zzzzzzzzzzz: Join this channel")}
    (guides / "other.md").write_text(BACKFILL_OTHER_GUIDE, encoding="utf-8")
    result = RUNNER.invoke(APP, ["backfill", ".", "--config", str(config_path)], env={"COLUMNS": "300"})
    assert result.exit_code == 1
    assert "1 written, 1 renamed, 0 unchanged, 1 with problems." in result.output
    assert "1 of 2 guides have problems:" in result.output
    assert "[youtube] zzzzzzzzzzz: Join this channel" in result.output
    assert sorted(child.name for child in guides.iterdir()) == [BACKFILL_NEW_NAME, "other.md"]
    assert (guides / "other.md").read_text(encoding="utf-8") == BACKFILL_OTHER_GUIDE


def test_backfill_command_reports_a_write_whose_rename_fails(
    cli_setup: tuple[Path, FakeLoader, list[bool]], guides: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path, _, _ = cli_setup

    def failing_rename(update: GuideUpdate) -> None:
        raise BackfillError(f"{update.path}: cannot rename it to {update.new_path.name} (Permission denied)")

    monkeypatch.setattr(backfill_cmd, "rename_guide", failing_rename)
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
    (guides / "copy.md").write_bytes(BACKFILL_GUIDE.encode("utf-8"))
    result = RUNNER.invoke(APP, ["backfill", ".", "--config", str(config_path)], env={"COLUMNS": "300"})
    assert result.exit_code == 1
    assert "No guide was changed, because of problems between guides" in result.output
    assert sorted(child.name for child in guides.iterdir()) == ["copy.md", "new.md"]


def test_backfill_command_missing_path(cli_setup: tuple[Path, FakeLoader, list[bool]]) -> None:
    config_path, _, _ = cli_setup
    result = RUNNER.invoke(APP, ["backfill", "missing.md", "--config", str(config_path)])
    assert result.exit_code == 1
    assert "No such file or folder: 'missing.md'" in result.output
