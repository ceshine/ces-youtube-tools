import json
import logging
import datetime
import subprocess
from pathlib import Path

import pytest

from youtube_tools.guide import GuideError
from youtube_tools.video import VideoInfo
from youtube_tools.ytdlp import YtDlp
from youtube_tools.config import AppConfig
from youtube_tools.pipeline import (
    write_guide,
    work_dir_for,
    load_transcript,
    load_video_info,
    load_prompt_template,
    make_guide_generator,
    make_transcript_provider,
)
from youtube_tools.subtitles import YtDlpSubtitleProvider
from youtube_tools.claude_cli import ClaudeCliGenerator
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


class FakeGenerator:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    def generate(self, prompt: str, work_dir: Path) -> str:
        self.prompts.append(prompt)
        return self.reply


TODAY = datetime.date(2026, 10, 9)
GUIDE_VIDEO = VideoInfo(id="abcdefghijk", title="A video: part 1", duration=600)
GUIDE_TRANSCRIPT = Transcript(
    video_id="abcdefghijk", source="fake", language="en", segments=[TranscriptSegment(start=0, end=1, text="hi")]
)
REPLY = "```markdown\n# Guide\n\n[1:10](https://www.youtube.com/watch?v=abcdefghijk&t=67s)\n```"


def test_write_guide(tmp_path: Path) -> None:
    generator = FakeGenerator(REPLY)
    work_dir, output_dir = tmp_path / "work", tmp_path / "out" / "nested"
    work_dir.mkdir()
    result = write_guide(GUIDE_VIDEO, GUIDE_TRANSCRIPT, "Guide for $title", generator, work_dir, output_dir, TODAY)
    assert result.path == output_dir / "Watching Guide A video part 1.md"
    assert (
        result.path.read_text(encoding="utf-8")
        == "# Guide\n\n[1:07](https://www.youtube.com/watch?v=abcdefghijk&t=67s)\n"
    )
    assert (result.links.links, result.links.relabeled) == (1, 1)
    assert generator.prompts == ["Guide for A video: part 1"]
    assert (work_dir / "prompt.md").read_text(encoding="utf-8") == "Guide for A video: part 1"
    assert (work_dir / "guide.md").read_text(encoding="utf-8") == REPLY


def test_write_guide_refuses_to_overwrite_before_generating(tmp_path: Path) -> None:
    existing = tmp_path / "Watching Guide A video part 1.md"
    existing.write_text("mine", encoding="utf-8")
    generator = FakeGenerator(REPLY)
    with pytest.raises(GuideError, match="already exists"):
        _ = write_guide(GUIDE_VIDEO, GUIDE_TRANSCRIPT, "$title", generator, tmp_path, tmp_path, TODAY)
    assert generator.prompts == []
    assert existing.read_text(encoding="utf-8") == "mine"
    _ = write_guide(GUIDE_VIDEO, GUIDE_TRANSCRIPT, "$title", generator, tmp_path, tmp_path, TODAY, force=True)
    assert existing.read_text(encoding="utf-8").startswith("# Guide")


def test_write_guide_rejects_bad_template_before_generating(tmp_path: Path) -> None:
    generator = FakeGenerator(REPLY)
    with pytest.raises(GuideError, match="Unknown placeholder"):
        _ = write_guide(GUIDE_VIDEO, GUIDE_TRANSCRIPT, "$nope", generator, tmp_path, tmp_path, TODAY)
    assert generator.prompts == []


def test_load_prompt_template(tmp_path: Path) -> None:
    text, is_default = load_prompt_template(tmp_path / "missing.md")
    assert is_default and "$transcript" in text
    custom = tmp_path / "custom.md"
    custom.write_text("Mine: $title", encoding="utf-8")
    assert load_prompt_template(custom) == ("Mine: $title", False)


def test_make_guide_generator() -> None:
    config = AppConfig()
    config.guide.options["claude-cli"] = {"model": "opus"}
    generator = make_guide_generator(config)
    assert isinstance(generator, ClaudeCliGenerator)
    assert generator.options.model == "opus"


def test_make_guide_generator_errors() -> None:
    config = AppConfig()
    config.guide.generator = "openai"
    with pytest.raises(ValueError, match="Unknown guide generator 'openai'. Available: claude-cli"):
        _ = make_guide_generator(config)
    config = AppConfig()
    config.guide.options["claude-cli"] = {"modle": "opus"}
    with pytest.raises(ValueError, match=r"Invalid settings in \[guide.options.claude-cli\]"):
        _ = make_guide_generator(config)
