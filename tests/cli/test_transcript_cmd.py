from pathlib import Path

import pytest
from typer.testing import CliRunner

from ces_youtube_tools.cli import APP, transcript_cmd
from ces_youtube_tools.video import VideoInfo
from ces_youtube_tools.ytdlp import YtDlp
from ces_youtube_tools.config import AppConfig, save_config
from ces_youtube_tools.transcript import Transcript, TranscriptSegment

RUNNER = CliRunner()


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

    monkeypatch.setattr(transcript_cmd, "load_video_info", fake_info)
    monkeypatch.setattr(transcript_cmd, "load_transcript", fake_transcript)
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
