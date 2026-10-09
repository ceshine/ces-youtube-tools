import json
import logging
import subprocess
from pathlib import Path

import pytest

from youtube_tools.video import VideoInfo
from youtube_tools.ytdlp import YtDlp
from youtube_tools.config import AppConfig
from youtube_tools.pipeline import work_dir_for, load_transcript, load_video_info, make_transcript_provider
from youtube_tools.subtitles import YtDlpSubtitleProvider
from youtube_tools.transcript import Transcript, TranscriptSegment

VIDEO_JSON = {"id": "abcdefghijk", "title": "A video", "formats": [{"url": "https://signed.example/x"}]}


class CountingRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        return subprocess.CompletedProcess(args, 0, json.dumps(VIDEO_JSON), "")


class FakeProvider:
    def __init__(self) -> None:
        self.calls = 0

    def fetch(self, video: VideoInfo, work_dir: Path) -> Transcript:
        self.calls += 1
        return Transcript(
            video_id=video.id, source="fake", language="en", segments=[TranscriptSegment(start=0, end=1, text="hi")]
        )


def test_work_dir_for(tmp_path: Path) -> None:
    work_dir = work_dir_for(tmp_path / "cache", "abcdefghijk")
    assert work_dir == tmp_path / "cache" / "abcdefghijk"
    assert work_dir.is_dir()


def test_load_video_info_caches_only_the_needed_fields(tmp_path: Path) -> None:
    runner = CountingRunner()
    ytdlp = YtDlp(["yt-dlp"], "firefox:/p", runner)
    first = load_video_info("abcdefghijk", ytdlp, tmp_path)
    second = load_video_info("abcdefghijk", ytdlp, tmp_path)
    assert first == second
    assert first.title == "A video"
    assert len(runner.calls) == 1
    assert runner.calls[0][-2:] == ["--skip-download", "https://www.youtube.com/watch?v=abcdefghijk"]
    assert "signed.example" not in (tmp_path / "video.json").read_text(encoding="utf-8")


def test_load_video_info_refresh(tmp_path: Path) -> None:
    runner = CountingRunner()
    ytdlp = YtDlp(["yt-dlp"], None, runner)
    _ = load_video_info("abcdefghijk", ytdlp, tmp_path)
    _ = load_video_info("abcdefghijk", ytdlp, tmp_path, refresh=True)
    assert len(runner.calls) == 2


def test_invalid_cache_is_rebuilt(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    (tmp_path / "video.json").write_text('{"not": "a video"}', encoding="utf-8")
    runner = CountingRunner()
    with caplog.at_level(logging.WARNING):
        info = load_video_info("abcdefghijk", YtDlp(["yt-dlp"], None, runner), tmp_path)
    assert info.title == "A video"
    assert len(runner.calls) == 1
    assert "Ignoring invalid cache file" in caplog.text


def test_load_transcript_caches(tmp_path: Path) -> None:
    provider = FakeProvider()
    video = VideoInfo(id="abcdefghijk", title="A video")
    first = load_transcript(video, provider, tmp_path)
    second = load_transcript(video, provider, tmp_path)
    assert first == second
    assert provider.calls == 1
    _ = load_transcript(video, provider, tmp_path, refresh=True)
    assert provider.calls == 2


def test_make_transcript_provider() -> None:
    config = AppConfig()
    config.transcript.languages = ["de"]
    provider = make_transcript_provider(config, YtDlp(["yt-dlp"], None))
    assert isinstance(provider, YtDlpSubtitleProvider)
    assert provider.languages == ["de"]


def test_make_transcript_provider_unknown() -> None:
    config = AppConfig()
    config.transcript.provider = "whisper"
    with pytest.raises(ValueError, match="Unknown transcript provider 'whisper'. Available: yt-dlp-subs"):
        _ = make_transcript_provider(config, YtDlp(["yt-dlp"], None))
