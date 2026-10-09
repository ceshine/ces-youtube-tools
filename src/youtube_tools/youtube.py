"""YouTube lookups built on yt-dlp."""

import re

from .ytdlp import YtDlp, YtDlpError
from .config import ChannelConfig

YOUTUBE_ORIGIN = "https://www.youtube.com"
SUBSCRIPTIONS_FEED_URL = f"{YOUTUBE_ORIGIN}/feed/subscriptions"
_CHANNEL_URL_PATTERN = re.compile(
    r"^(?:https?://)?(?:www\.|m\.)?youtube\.com/(@[\w.-]+|channel/UC[\w-]{22}|c/[\w.-]+|user/[\w.-]+)(?:/.*)?$"
)
_HANDLE_PATTERN = re.compile(r"^@[\w.-]+$")


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
