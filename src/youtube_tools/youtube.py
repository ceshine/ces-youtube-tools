"""YouTube lookups built on yt-dlp."""

import re
import heapq
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from pydantic import BaseModel

from .ytdlp import YtDlp, YtDlpError
from .config import ChannelConfig

YOUTUBE_ORIGIN = "https://www.youtube.com"
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
        return f"{YOUTUBE_ORIGIN}/watch?v={self.id}"


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


def fetch_playlist(url: str, ytdlp: YtDlp, limit: int, members_only: bool = False) -> list[VideoSummary]:
    """Fetch the newest entries of a playlist, with approximate publish times.

    Args:
        url (str): The playlist URL.
        ytdlp (YtDlp): The yt-dlp runner.
        limit (int): Maximum number of entries to fetch.
        members_only (bool): Mark the entries as members-only.

    Returns:
        list[VideoSummary]: Entries in playlist order (newest first). Empty if the playlist does not exist, e.g. a
            channel without livestreams or memberships.

    Raises:
        YtDlpError: If yt-dlp fails for another reason.
    """
    args = ["--flat-playlist", "--extractor-args", "youtubetab:approximate_date", "--playlist-end", str(limit), url]
    try:
        data = ytdlp.run_json(args)
    except YtDlpError as exc:
        if _MISSING_PLAYLIST_MESSAGE in str(exc):
            return []
        raise
    entries: list[dict[str, Any]] = [entry for entry in data.get("entries") or [] if entry and entry.get("id")]
    return [_to_summary(entry, members_only) for entry in entries]


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
    sources = [(prefix, False) for prefix in PUBLIC_PLAYLIST_PREFIXES]
    if channel.members:
        sources += [(prefix, True) for prefix in MEMBERS_PLAYLIST_PREFIXES]
    with ThreadPoolExecutor(max_workers=len(sources)) as pool:
        futures = [
            pool.submit(fetch_playlist, channel_playlist_url(channel.channel_id, prefix), ytdlp, limit, members_only)
            for prefix, members_only in sources
        ]
        playlists = [future.result() for future in futures]
    videos = [video for video in merge_newest_first(playlists) if video.live_status not in _SKIPPED_LIVE_STATUSES]
    return videos[:limit]
