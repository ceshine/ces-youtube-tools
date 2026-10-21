"""YouTube lookups built on yt-dlp."""

import re
import heapq
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from .video import video_url, YOUTUBE_ORIGIN
from .ytdlp import YtDlp, YtDlpError
from .config import ChannelConfig

SUBSCRIPTIONS_FEED_URL = f"{YOUTUBE_ORIGIN}/feed/subscriptions"
_CHANNEL_URL_PATTERN = re.compile(
    r"^(?:https?://)?(?:www\.|m\.)?youtube\.com/(@[\w.-]+|channel/UC[\w-]{22}|c/[\w.-]+|user/[\w.-]+)(?:/.*)?$"
)
_HANDLE_PATTERN = re.compile(r"^@[\w.-]+$")

# Channel upload playlists, named by prefix + channel ID without "UC". They exclude Shorts, unlike `UU`.
# See docs/implementation_notes.md.
PUBLIC_PLAYLIST_PREFIXES = ("UULF", "UULV")  # long-form videos, livestreams
MEMBERS_PLAYLIST_PREFIXES = ("UUMF", "UUMV")  # members-only videos, members-only livestreams
_MISSING_PLAYLIST_MESSAGE = "The playlist does not exist"
# Not yet finished, so they have no subtitles to build a guide from.
_SKIPPED_LIVE_STATUSES = frozenset({"is_live", "is_upcoming"})


class VideoSummary(BaseModel):
    """A video from a channel listing.

    Attributes:
        id (str): YouTube video ID.
        title (str): Video title.
        duration (float | None): Length in seconds, if known.
        timestamp (int | None): Approximate publish time as a Unix timestamp. Recent videos are accurate to the hour,
            older ones only roughly.
        live_status (str | None): `was_live` for livestream replays, otherwise None.
        members_only (bool): Whether only channel members can watch it.
    """

    id: str
    title: str
    duration: float | None = None
    timestamp: int | None = None
    live_status: str | None = None
    members_only: bool = False

    @property
    def url(self) -> str:
        """The video's watch URL.

        Returns:
            str: The URL.
        """
        return video_url(self.id)


def normalize_channel_url(value: str) -> str:
    """Turn a handle or channel URL into a canonical channel URL without a tab suffix.

    Args:
        value (str): A handle such as `@3blue1brown`, or a channel URL, optionally ending in a tab like `/videos`.

    Returns:
        str: The channel URL, e.g. `https://www.youtube.com/@3blue1brown`.

    Raises:
        ValueError: If the value is not a recognizable channel handle or URL.
    """
    value = value.strip()
    if _HANDLE_PATTERN.match(value):
        return f"{YOUTUBE_ORIGIN}/{value}"
    match = _CHANNEL_URL_PATTERN.match(value)
    if match is None:
        raise ValueError(f"Not a YouTube channel handle or URL: {value!r}")
    return f"{YOUTUBE_ORIGIN}/{match.group(1)}"


def resolve_channel(value: str, ytdlp: YtDlp) -> ChannelConfig:
    """Look up a channel's name and ID.

    Args:
        value (str): A channel handle or URL.
        ytdlp (YtDlp): The yt-dlp runner.

    Returns:
        ChannelConfig: The channel, with members-only listing turned off.

    Raises:
        ValueError: If `value` is not a channel handle or URL.
        YtDlpError: If yt-dlp fails or the result has no channel ID.
    """
    url = normalize_channel_url(value)
    data = ytdlp.run_json(["--flat-playlist", "--playlist-items", "1", url])
    channel_id = data.get("channel_id")
    if not channel_id:
        raise YtDlpError(f"yt-dlp returned no channel ID for {url}")
    return ChannelConfig(name=data.get("channel") or data.get("title") or url, url=url, channel_id=channel_id)


def verify_login(ytdlp: YtDlp) -> bool:
    """Check that YouTube accepts the cookies by reading the subscriptions feed, which needs a login.

    Args:
        ytdlp (YtDlp): The yt-dlp runner, configured with cookies.

    Returns:
        bool: True if the feed could be read.
    """
    try:
        _ = ytdlp.run(["--flat-playlist", "--playlist-items", "1", "--print", "id", SUBSCRIPTIONS_FEED_URL])
    except YtDlpError:
        return False
    return True


def channel_playlist_url(channel_id: str, prefix: str) -> str:
    """Build the URL of one of a channel's upload playlists.

    Args:
        channel_id (str): YouTube channel ID, starting with `UC`.
        prefix (str): Playlist prefix such as `UULF`.

    Returns:
        str: The playlist URL.

    Raises:
        ValueError: If `channel_id` does not start with `UC`.
    """
    if not channel_id.startswith("UC"):
        raise ValueError(f"Channel ID should start with 'UC': {channel_id!r}")
    return f"{YOUTUBE_ORIGIN}/playlist?list={prefix}{channel_id[2:]}"


def _to_summary(entry: dict[str, Any], members_only: bool) -> VideoSummary:
    """Convert a flat-playlist entry from yt-dlp into a summary.

    Args:
        entry (dict[str, Any]): The entry.
        members_only (bool): Whether the entry came from a members-only playlist.

    Returns:
        VideoSummary: The summary.
    """
    return VideoSummary(
        id=entry["id"],
        title=entry.get("title") or entry["id"],
        duration=entry.get("duration"),
        timestamp=entry.get("timestamp"),
        live_status=entry.get("live_status"),
        members_only=members_only,
    )


@dataclass(frozen=True)
class PlaylistFetch:
    """The newest entries of one playlist.

    Attributes:
        videos (list[VideoSummary]): Entries in playlist order (newest first).
        full (bool): Whether yt-dlp returned as many entries as were asked for, counting entries dropped for having
            no ID. The playlist may then hold more.
    """

    videos: list[VideoSummary]
    full: bool


def fetch_playlist(url: str, ytdlp: YtDlp, limit: int, members_only: bool = False) -> PlaylistFetch:
    """Fetch the newest entries of a playlist, with approximate publish times.

    Args:
        url (str): The playlist URL.
        ytdlp (YtDlp): The yt-dlp runner.
        limit (int): Maximum number of entries to fetch.
        members_only (bool): Mark the entries as members-only.

    Returns:
        PlaylistFetch: The entries. Empty and not full if the playlist does not exist, e.g. a channel without
            livestreams or memberships.

    Raises:
        YtDlpError: If yt-dlp fails for another reason.
    """
    args = ["--flat-playlist", "--extractor-args", "youtubetab:approximate_date", "--playlist-end", str(limit), url]
    try:
        data = ytdlp.run_json(args)
    except YtDlpError as exc:
        if _MISSING_PLAYLIST_MESSAGE in str(exc):
            return PlaylistFetch(videos=[], full=False)
        raise
    # Count the raw entries: a playlist with a dropped entry would otherwise look exhausted.
    raw_entries: list[dict[str, Any] | None] = data.get("entries") or []
    entries: list[dict[str, Any]] = [entry for entry in raw_entries if entry and entry.get("id")]
    return PlaylistFetch(videos=[_to_summary(entry, members_only) for entry in entries], full=len(raw_entries) >= limit)


def merge_newest_first(playlists: list[list[VideoSummary]]) -> list[VideoSummary]:
    """Merge newest-first playlists into one newest-first list, dropping duplicates.

    Each playlist keeps its own order. Timestamps are only used to interleave playlists, because they are approximate.

    Args:
        playlists (list[list[VideoSummary]]): Playlists, each newest first.

    Returns:
        list[VideoSummary]: The merged list. Entries without a timestamp sort as oldest.
    """
    merged = heapq.merge(*playlists, key=lambda video: video.timestamp or 0, reverse=True)
    seen: set[str] = set()
    result: list[VideoSummary] = []
    for video in merged:
        if video.id not in seen:
            seen.add(video.id)
            result.append(video)
    return result


def _fetch_merged(channel: ChannelConfig, ytdlp: YtDlp, depth: int) -> tuple[list[VideoSummary], bool]:
    """Fetch a channel's upload playlists in parallel and merge them newest first.

    Members-only playlists are fetched only when the channel has `members` set. Livestreams that are live now or
    upcoming are left out.

    Args:
        channel (ChannelConfig): The channel.
        ytdlp (YtDlp): The yt-dlp runner. Cookies are not needed.
        depth (int): Number of entries to fetch from each playlist.

    Returns:
        tuple[list[VideoSummary], bool]: The merged videos, and whether any playlist was full, so may hold more.

    Raises:
        YtDlpError: If yt-dlp fails for a reason other than a missing playlist.
    """
    sources = [(prefix, False) for prefix in PUBLIC_PLAYLIST_PREFIXES]
    if channel.members:
        sources += [(prefix, True) for prefix in MEMBERS_PLAYLIST_PREFIXES]
    with ThreadPoolExecutor(max_workers=len(sources)) as pool:
        futures = [
            pool.submit(fetch_playlist, channel_playlist_url(channel.channel_id, prefix), ytdlp, depth, members_only)
            for prefix, members_only in sources
        ]
        fetches = [future.result() for future in futures]
    merged = merge_newest_first([fetch.videos for fetch in fetches])
    videos = [video for video in merged if video.live_status not in _SKIPPED_LIVE_STATUSES]
    return videos, any(fetch.full for fetch in fetches)


def list_latest_videos(channel: ChannelConfig, ytdlp: YtDlp, limit: int = 20) -> list[VideoSummary]:
    """List a channel's latest videos and livestream replays, newest first, excluding Shorts.

    Members-only uploads are included only when the channel has `members` set. Livestreams that are live now or
    upcoming are left out.

    Args:
        channel (ChannelConfig): The channel.
        ytdlp (YtDlp): The yt-dlp runner. Cookies are not needed.
        limit (int): Maximum number of videos to return.

    Returns:
        list[VideoSummary]: The latest videos.

    Raises:
        YtDlpError: If yt-dlp fails for a reason other than a missing playlist.
    """
    return _fetch_merged(channel, ytdlp, limit)[0][:limit]


class VideoPager:
    """Lists a channel's videos a page at a time, newest first, like `list_latest_videos`.

    Each new page fetches every playlist again, deeper, and drops the videos already shown. A later fetch can order
    the videos differently (approximate timestamps are re-derived, new uploads shift the list, dropped livestreams
    let deeper entries move up), so slicing the merged list could skip a video or show one twice.

    Args:
        channel (ChannelConfig): The channel.
        ytdlp (YtDlp): The yt-dlp runner. Cookies are not needed.
        page_size (int): Number of videos per page.

    Attributes:
        channel (ChannelConfig): The channel.
        ytdlp (YtDlp): The yt-dlp runner.
        page_size (int): Number of videos per page. The last page can hold fewer.

    Raises:
        ValueError: If `page_size` is less than 1.
    """

    def __init__(self, channel: ChannelConfig, ytdlp: YtDlp, page_size: int) -> None:
        if page_size < 1:
            raise ValueError(f"Page size should be at least 1: {page_size}")
        self.channel: ChannelConfig = channel
        self.ytdlp: YtDlp = ytdlp
        self.page_size: int = page_size
        self._pages: list[list[VideoSummary]] = []
        self._has_more: bool = True

    @property
    def page_count(self) -> int:
        """Number of pages fetched so far.

        Returns:
            int: The count.
        """
        return len(self._pages)

    @property
    def has_more(self) -> bool:
        """Whether there may be videos after the last fetched page.

        It can only be wrong by saying yes: the next page is then empty, and this becomes False.

        Returns:
            bool: True if the next page may hold videos.
        """
        return self._has_more

    def fetched(self) -> list[VideoSummary]:
        """All videos on the fetched pages, in page order (newest first).

        Returns:
            list[VideoSummary]: The videos.
        """
        return [video for page in self._pages for video in page]

    def page(self, number: int) -> list[VideoSummary]:
        """Return a page, fetching it if it is the one after the last fetched page.

        A fetch that fails leaves the pager as it was, so it can be retried.

        Args:
            number (int): 1-based page number, at most `page_count + 1`.

        Returns:
            list[VideoSummary]: The page's videos. Empty, and not kept, when no videos are left.

        Raises:
            ValueError: If the page is neither fetched nor the next one.
            YtDlpError: If yt-dlp fails for a reason other than a missing playlist.
        """
        if 1 <= number <= len(self._pages):
            return self._pages[number - 1]
        if number != len(self._pages) + 1:
            raise ValueError(f"Cannot fetch page {number} after {len(self._pages)} fetched pages")
        shown = {video.id for video in self.fetched()}
        merged, any_full = _fetch_merged(self.channel, self.ytdlp, len(shown) + self.page_size + 1)
        left = [video for video in merged if video.id not in shown]
        page = left[: self.page_size]
        self._has_more = bool(page) and (len(left) > self.page_size or any_full)
        if page:
            self._pages.append(page)
        return page
