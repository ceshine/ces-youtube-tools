import json
import threading
import subprocess

import pytest

from ces_youtube_tools.ytdlp import YtDlp, YtDlpError
from ces_youtube_tools.config import ChannelConfig
from ces_youtube_tools.youtube import (
    VideoPager,
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
    fetched = fetch_playlist(url("UUMV"), YtDlp(["yt-dlp"], None, runner), limit=3, members_only=True)
    assert fetched.videos == [
        VideoSummary(id="a", title="Title a", duration=600, timestamp=100, live_status="was_live", members_only=True)
    ]
    # Three entries came back for a limit of 3, though two were dropped, so the playlist may hold more.
    assert fetched.full
    args = runner.calls[0]
    assert ["--extractor-args", "youtubetab:approximate_date"] == args[args.index("--extractor-args") :][:2]
    assert args[args.index("--playlist-end") + 1] == "3"


def test_fetch_playlist_short_playlist_is_not_full() -> None:
    runner = UrlRunner({url("UULF"): playlist(entry("a", 100), entry("b", 50))})
    assert not fetch_playlist(url("UULF"), YtDlp(["yt-dlp"], None, runner), limit=3).full


def test_fetch_playlist_missing_playlist_is_empty() -> None:
    fetched = fetch_playlist(url("UULV"), YtDlp(["yt-dlp"], None, UrlRunner({url("UULV"): MISSING})), limit=5)
    assert fetched.videos == []
    assert not fetched.full


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


class SequenceRunner:
    """Answers each playlist URL's calls in turn from a list of responses, and records each call's `--playlist-end`.

    Thread-safe.
    """

    def __init__(self, responses: dict[str, list[subprocess.CompletedProcess[str]]]) -> None:
        self.responses = {key: list(answers) for key, answers in responses.items()}
        self.ends: dict[str, list[int]] = {key: [] for key in responses}
        self._lock = threading.Lock()

    def __call__(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        with self._lock:
            self.ends[args[-1]].append(int(args[args.index("--playlist-end") + 1]))
            return self.responses[args[-1]].pop(0)


def make_pager(runner: SequenceRunner | UrlRunner, page_size: int = 2, members: bool = False) -> VideoPager:
    return VideoPager(make_channel(members=members), YtDlp(["yt-dlp"], None, runner), page_size)


def ids(videos: list[VideoSummary]) -> list[str]:
    return [video.id for video in videos]


def test_pager_fetches_deeper_for_each_page_and_caches_pages() -> None:
    newest = [entry(f"v{n}", 1000 - n) for n in range(1, 6)]
    runner = SequenceRunner({url("UULF"): [playlist(*newest[:3]), playlist(*newest)], url("UULV"): [MISSING, MISSING]})
    pager = make_pager(runner)
    assert ids(pager.page(1)) == ["v1", "v2"]
    assert pager.has_more
    assert ids(pager.page(2)) == ["v3", "v4"]
    assert runner.ends[url("UULF")] == [3, 5]

    assert ids(pager.page(1)) == ["v1", "v2"]
    assert runner.ends[url("UULF")] == [3, 5]
    assert pager.page_count == 2
    assert ids(pager.fetched()) == ["v1", "v2", "v3", "v4"]


def test_pager_rejects_a_page_it_cannot_fetch_yet() -> None:
    pager = make_pager(SequenceRunner({}))
    for number in (0, 2):
        with pytest.raises(ValueError, match=f"Cannot fetch page {number}"):
            _ = pager.page(number)


def test_pager_neither_skips_nor_repeats_when_a_later_fetch_reorders() -> None:
    # The second fetch re-derives b's approximate timestamp as newer than s. Slicing the merged list at 2 would
    # then repeat s and skip b.
    runner = SequenceRunner(
        {
            url("UULF"): [
                playlist(entry("a", 500), entry("b", 400)),
                playlist(entry("a", 500), entry("b", 460), entry("c", 300)),
            ],
            url("UULV"): [playlist(entry("s", 450), entry("t", 100)), playlist(entry("s", 450), entry("t", 100))],
        }
    )
    pager = make_pager(runner)
    assert ids(pager.page(1)) == ["a", "s"]
    assert ids(pager.page(2)) == ["b", "c"]


def test_pager_has_no_more_after_short_playlists() -> None:
    runner = SequenceRunner({url("UULF"): [playlist(entry("v1", 300), entry("v2", 200))], url("UULV"): [MISSING]})
    pager = make_pager(runner)
    assert ids(pager.page(1)) == ["v1", "v2"]
    assert not pager.has_more


def test_pager_empty_page_is_not_kept_and_ends_the_listing() -> None:
    # A full playlist suggests more videos, but the extra entry is an upcoming stream, which is left out.
    page = playlist(entry("v1", 300), entry("v2", 200), entry("soon", 100, live_status="is_upcoming"))
    runner = SequenceRunner({url("UULF"): [page, page], url("UULV"): [MISSING, MISSING]})
    pager = make_pager(runner)
    assert ids(pager.page(1)) == ["v1", "v2"]
    assert pager.has_more
    assert pager.page(2) == []
    assert not pager.has_more
    assert pager.page_count == 1


def test_pager_counts_a_playlist_with_a_dropped_entry_as_full() -> None:
    runner = SequenceRunner(
        {url("UULF"): [playlist(entry("v1", 300), {"id": None}, entry("v2", 200))], url("UULV"): [MISSING]}
    )
    pager = make_pager(runner)
    assert ids(pager.page(1)) == ["v1", "v2"]
    assert pager.has_more


def test_pager_failed_fetch_leaves_it_unchanged_for_a_retry() -> None:
    newest = [entry(f"v{n}", 1000 - n) for n in range(1, 6)]
    failure = subprocess.CompletedProcess([], 1, "", "ERROR: [youtube:tab] x: HTTP Error 500\n")
    runner = SequenceRunner(
        {
            url("UULF"): [playlist(*newest[:3]), failure, playlist(*newest)],
            url("UULV"): [MISSING, MISSING, MISSING],
        }
    )
    pager = make_pager(runner)
    _ = pager.page(1)
    with pytest.raises(YtDlpError, match="HTTP Error 500"):
        _ = pager.page(2)
    assert pager.page_count == 1
    assert pager.has_more
    assert ids(pager.fetched()) == ["v1", "v2"]
    assert ids(pager.page(2)) == ["v3", "v4"]


@pytest.mark.parametrize("members", [False, True])
def test_pager_fetches_members_playlists_only_for_members(members: bool) -> None:
    runner = UrlRunner(
        {
            url("UULF"): playlist(entry("v1", 300)),
            url("UULV"): MISSING,
            url("UUMF"): playlist(entry("m1", 400)),
            url("UUMV"): MISSING,
        }
    )
    page = make_pager(runner, members=members).page(1)
    assert ids(page) == (["m1", "v1"] if members else ["v1"])
    expected = {url("UULF"), url("UULV")} | ({url("UUMF"), url("UUMV")} if members else set())
    assert {call[-1] for call in runner.calls} == expected
