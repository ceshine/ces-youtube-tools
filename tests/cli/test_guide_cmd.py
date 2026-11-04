from pathlib import Path

import pytest
from typer.testing import CliRunner

from ces_youtube_tools import pipeline
from ces_youtube_tools.cli import APP
from ces_youtube_tools.video import VideoInfo
from ces_youtube_tools.ytdlp import YtDlp, YtDlpError
from ces_youtube_tools.config import load_config, save_config
from ces_youtube_tools.transcript import Transcript

from .conftest import OLD_GUIDE, OLD_LINKS, guide_body, GUIDE_REPLY, FakeGuideGenerator

RUNNER = CliRunner()


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

    monkeypatch.setattr(pipeline, "load_video_info", bracket_info)
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

    monkeypatch.setattr(pipeline, "load_video_info", failing_info)
    args = ["guide", "3i2C3IB3PaA", "-o", str(tmp_path), "--config", str(config_path)]
    result = RUNNER.invoke(APP, args, env={"COLUMNS": "1000"})
    assert result.exit_code == 1
    assert "ERROR: [youtube] 3i2C3IB3PaA: Join this channel" in result.output


def test_guide_without_upload_date_leaves_the_date_out_of_the_file_name(
    guide_setup: FakeGuideGenerator, config_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def undated_info(video_id: str, ytdlp: YtDlp, work_dir: Path, refresh: bool = False) -> VideoInfo:
        return VideoInfo(id=video_id, title="A video", duration=60)

    monkeypatch.setattr(pipeline, "load_video_info", undated_info)
    args = ["guide", "3i2C3IB3PaA", "-o", str(tmp_path), "--config", str(config_path)]
    result = RUNNER.invoke(APP, args)
    assert result.exit_code == 0, result.output
    assert guide_body(tmp_path / "Watching Guide - A video.md") == GUIDE_REPLY


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


def test_guide_never_replaces_a_file_found_only_by_its_links(
    guide_setup: FakeGuideGenerator, config_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    notes = tmp_path / "My notes.md"
    notes.write_text(OLD_LINKS, encoding="utf-8")
    fetched: list[str] = []

    def no_transcript(video: VideoInfo, provider: object, work_dir: Path, refresh: bool = False) -> Transcript:
        fetched.append(video.id)
        raise AssertionError("the transcript must not be fetched")

    monkeypatch.setattr(pipeline, "load_transcript", no_transcript)
    args = ["guide", "3i2C3IB3PaA", "-o", str(tmp_path), "--force", "--config", str(config_path)]
    result = RUNNER.invoke(APP, args, env={"COLUMNS": "1000"})
    assert result.exit_code == 1
    assert f"{notes} links to this video, but its front matter does not name it" in result.output
    assert fetched == []
    assert notes.read_text(encoding="utf-8") == OLD_LINKS
