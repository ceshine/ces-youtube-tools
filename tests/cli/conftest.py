from pathlib import Path

import pytest

from ces_youtube_tools import pipeline
from ces_youtube_tools.video import VideoInfo
from ces_youtube_tools.ytdlp import YtDlp
from ces_youtube_tools.config import AppConfig, save_config, ChannelConfig
from ces_youtube_tools.transcript import Transcript, TranscriptSegment
from ces_youtube_tools.front_matter import split_front_matter

CHANNEL = ChannelConfig(name="3Blue1Brown", url="https://www.youtube.com/@3blue1brown", channel_id="UCYO")


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    return tmp_path / "config" / "config.toml"


# The guide samples, shared by tests/cli/test_guide_cmd.py and tests/cli/test_browse_cmd.py.


def guide_body(path: Path) -> str:
    return split_front_matter(path.read_text(encoding="utf-8"))[1]


GUIDE_REPLY = "# Watching Guide: A video\n\n[0:05](https://www.youtube.com/watch?v=3i2C3IB3PaA&t=5s)\n"

OLD_LINKS = "# Old guide\n\n[0:05](https://www.youtube.com/watch?v=3i2C3IB3PaA&t=5s)\n"
OLD_GUIDE = "---\nvideo_id: 3i2C3IB3PaA\n---\n\n" + OLD_LINKS


class FakeGuideGenerator:
    def __init__(self, reply: str = GUIDE_REPLY) -> None:
        self.reply = reply
        self.prompts: list[str] = []
        self.harness: str = "Fake harness"
        self.model: str = "fake-model"
        self.effort: str | None = None

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
