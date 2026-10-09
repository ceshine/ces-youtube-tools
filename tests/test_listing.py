import json
import threading
import subprocess

import pytest

from youtube_tools.ytdlp import YtDlp, YtDlpError
from youtube_tools.config import ChannelConfig
from youtube_tools.youtube import (
    VideoSummary,
    fetch_playlist,
    list_latest_videos,
    merge_newest_first,
    channel_playlist_url,
)

CHANNEL_ID = "UC1yBKRuGpC1tSM73A0ZjYjQ"
MISSING = subprocess.CompletedProcess(
    [], 1, "null", "ERROR: [youtube:tab] x: YouTube said: The playlist does not exist.\n"
)


def entry(video_id: str, timestamp: int | None, **extra: object) -> dict[str, object]:
    return {"id": video_id, "title": f"Title {video_id}", "duration": 600, "timestamp": timestamp, **extra}


def playlist(*entries: dict[str, object]) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], 0, json.dumps({"entries": list(entries)}), "")


class UrlRunner:
    """Answers each yt-dlp call by the playlist URL at the end of its arguments. Thread-safe."""

    def __init__(self, responses: dict[str, subprocess.CompletedProcess[str]]) -> None:
        self.responses = responses
        self.calls: list[list[str]] = []
        self._lock = threading.Lock()

    def __call__(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        with self._lock:
            self.calls.append(args)
        return self.responses[args[-1]]


def url(prefix: str) -> str:
    return channel_playlist_url(CHANNEL_ID, prefix)


def test_channel_playlist_url() -> None:
    assert url("UULF") == "https://www.youtube.com/playlist?list=UULF1yBKRuGpC1tSM73A0ZjYjQ"


def test_channel_playlist_url_rejects_non_channel_id() -> None:
    with pytest.raises(ValueError, match="should start with 'UC'"):
        _ = channel_playlist_url("@handle", "UULF")


def test_fetch_playlist_parses_entries_and_asks_for_approximate_dates() -> None:
    runner = UrlRunner({url("UUMV"): playlist(entry("a", 100, live_status="was_live"), {"id": None}, {})})
    videos = fetch_playlist(url("UUMV"), YtDlp(["yt-dlp"], None, runner), limit=5, members_only=True)
    assert videos == [
        VideoSummary(id="a", title="Title a", duration=600, timestamp=100, live_status="was_live", members_only=True)
    ]
    args = runner.calls[0]
    assert ["--extractor-args", "youtubetab:approximate_date"] == args[args.index("--extractor-args") :][:2]
    assert args[args.index("--playlist-end") + 1] == "5"


def test_fetch_playlist_missing_playlist_is_empty() -> None:
    assert fetch_playlist(url("UULV"), YtDlp(["yt-dlp"], None, UrlRunner({url("UULV"): MISSING})), limit=5) == []


def test_fetch_playlist_other_errors_propagate() -> None:
    failure = subprocess.CompletedProcess([], 1, "", "ERROR: [youtube:tab] x: HTTP Error 500\n")
    with pytest.raises(YtDlpError, match="HTTP Error 500"):
        _ = fetch_playlist(url("UULF"), YtDlp(["yt-dlp"], None, UrlRunner({url("UULF"): failure})), limit=5)


def summary(video_id: str, timestamp: int | None) -> VideoSummary:
    return VideoSummary(id=video_id, title=video_id, timestamp=timestamp)


def test_merge_newest_first_interleaves_and_keeps_playlist_order() -> None:
    # Equal approximate timestamps must not reorder entries within a playlist.
    videos = [summary("v1", 300), summary("v2", 300), summary("v3", 100)]
    streams = [summary("s1", 200), summary("s2", 50)]
    assert [v.id for v in merge_newest_first([videos, streams])] == ["v1", "v2", "s1", "v3", "s2"]


def test_merge_newest_first_drops_duplicates_and_sorts_unknown_last() -> None:
    merged = merge_newest_first([[summary("a", 200), summary("b", None)], [summary("a", 200), summary("c", 100)]])
    assert [v.id for v in merged] == ["a", "c", "b"]


def make_channel(members: bool) -> ChannelConfig:
    return ChannelConfig(
        name="TYT", url="https://www.youtube.com/@TheYoungTurks", channel_id=CHANNEL_ID, members=members
    )


def test_list_latest_videos_public_only() -> None:
    runner = UrlRunner(
        {
            url("UULF"): playlist(entry("v1", 300), entry("v2", 100)),
            url("UULV"): playlist(entry("live", 400, live_status="is_live"), entry("s1", 200, live_status="was_live")),
        }
    )
    videos = list_latest_videos(make_channel(members=False), YtDlp(["yt-dlp"], None, runner), limit=10)
    assert [v.id for v in videos] == ["v1", "s1", "v2"]
    assert {call[-1] for call in runner.calls} == {url("UULF"), url("UULV")}


def test_list_latest_videos_with_members_and_limit() -> None:
    runner = UrlRunner(
        {
            url("UULF"): playlist(entry("v1", 300)),
            url("UULV"): MISSING,
            url("UUMF"): playlist(entry("m1", 400), entry("m2", 50)),
            url("UUMV"): playlist(entry("upcoming", 500, live_status="is_upcoming"), entry("ms1", 200)),
        }
    )
    videos = list_latest_videos(make_channel(members=True), YtDlp(["yt-dlp"], None, runner), limit=3)
    assert [(v.id, v.members_only) for v in videos] == [("m1", True), ("v1", False), ("ms1", True)]
    assert len(runner.calls) == 4
