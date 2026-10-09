import subprocess
from pathlib import Path

import pytest

from youtube_tools.video import VideoInfo
from youtube_tools.ytdlp import YtDlp, YtDlpError
from youtube_tools.subtitles import choose_track, SubtitleTrack, YtDlpSubtitleProvider
from youtube_tools.transcript import TranscriptError

VTT = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello there.\n"
LANGUAGES = ["en-orig", "en"]


def video(manual: list[str], auto: list[str]) -> VideoInfo:
    return VideoInfo(id="abcdefghijk", title="T", subtitle_languages=manual, auto_caption_languages=auto)


@pytest.mark.parametrize(
    ("manual", "auto", "expected"),
    [
        (["en"], ["en-orig", "en"], SubtitleTrack("en", automatic=False)),  # human-made beats automatic
        (["fr", "en-GB"], ["en-orig"], SubtitleTrack("en-GB", automatic=False)),  # regional match
        (["en-GB", "en"], [], SubtitleTrack("en", automatic=False)),  # exact match beats regional
        ([], ["en", "en-orig"], SubtitleTrack("en-orig", automatic=True)),  # preference order, not listing order
        ([], ["en"], SubtitleTrack("en", automatic=True)),
        (["fr"], ["fr-orig", "de"], None),
    ],
)
def test_choose_track(manual: list[str], auto: list[str], expected: SubtitleTrack | None) -> None:
    assert choose_track(video(manual, auto), LANGUAGES) == expected


def test_choose_track_automatic_needs_exact_match() -> None:
    assert choose_track(video([], ["en-US"]), ["en"]) is None


class WritingRunner:
    """Fakes yt-dlp: writes the requested subtitle file into the --paths directory."""

    def __init__(self, write: bool = True, returncode: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.write = write
        self.returncode = returncode

    def __call__(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        if self.write:
            directory = Path(args[args.index("--paths") + 1])
            language = args[args.index("--sub-langs") + 1]
            (directory / f"abcdefghijk.{language}.vtt").write_text(VTT, encoding="utf-8")
        stderr = "" if self.returncode == 0 else "ERROR: [youtube] abcdefghijk: Join this channel\n"
        return subprocess.CompletedProcess(args, self.returncode, "", stderr)


def test_fetch_automatic_captions(tmp_path: Path) -> None:
    runner = WritingRunner()
    provider = YtDlpSubtitleProvider(YtDlp(["yt-dlp"], "firefox:/p", runner), LANGUAGES)
    transcript = provider.fetch(video([], ["en-orig"]), tmp_path)
    assert transcript.source == "yt-dlp-subs: automatic en-orig"
    assert transcript.language == "en-orig"
    assert [segment.text for segment in transcript.segments] == ["Hello there."]
    args = runner.calls[0]
    assert args[:3] == ["yt-dlp", "--cookies-from-browser", "firefox:/p"]
    assert "--write-auto-subs" in args and "--write-subs" not in args
    assert args[args.index("--sub-format") + 1] == "vtt"
    assert args[-1] == "https://www.youtube.com/watch?v=abcdefghijk"
    assert (tmp_path / "subtitles" / "abcdefghijk.en-orig.vtt").exists()


def test_fetch_human_made_subtitles(tmp_path: Path) -> None:
    runner = WritingRunner()
    transcript = YtDlpSubtitleProvider(YtDlp(["yt-dlp"], None, runner), LANGUAGES).fetch(video(["en"], []), tmp_path)
    assert transcript.source == "yt-dlp-subs: human-made en"
    assert "--write-subs" in runner.calls[0]


def test_fetch_without_matching_track_lists_what_exists(tmp_path: Path) -> None:
    runner = WritingRunner()
    provider = YtDlpSubtitleProvider(YtDlp(["yt-dlp"], None, runner), LANGUAGES)
    with pytest.raises(TranscriptError, match=r"Human-made: fr\. Automatic: de, es\."):
        _ = provider.fetch(video(["fr"], ["de", "es"]), tmp_path)
    assert runner.calls == []


def test_fetch_when_no_file_is_written(tmp_path: Path) -> None:
    provider = YtDlpSubtitleProvider(YtDlp(["yt-dlp"], None, WritingRunner(write=False)), LANGUAGES)
    with pytest.raises(TranscriptError, match="did not write"):
        _ = provider.fetch(video(["en"], []), tmp_path)


def test_fetch_propagates_ytdlp_errors(tmp_path: Path) -> None:
    provider = YtDlpSubtitleProvider(YtDlp(["yt-dlp"], None, WritingRunner(write=False, returncode=1)), LANGUAGES)
    with pytest.raises(YtDlpError, match="Join this channel"):
        _ = provider.fetch(video(["en"], []), tmp_path)
