"""Video metadata from yt-dlp."""

import re
import datetime
from typing import Any

from pydantic import Field, BaseModel

YOUTUBE_ORIGIN = "https://www.youtube.com"
_VIDEO_ID_PATTERN = re.compile(r"^[\w-]{11}$")
_VIDEO_URL_PATTERN = re.compile(
    r"^(?:https?://)?(?:www\.|m\.|music\.)?(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|live/|embed/)|youtu\.be/)"
    r"([\w-]{11})(?:[?&#/].*)?$"
)
# Not a subtitle track: yt-dlp lists livestream chat replays among the subtitles.
_NON_SUBTITLE_TRACKS = frozenset({"live_chat"})


class Chapter(BaseModel):
    """A chapter marker set by the uploader.

    Attributes:
        start (float): Start time in seconds.
        end (float | None): End time in seconds, if known.
        title (str): Chapter title.
    """

    start: float
    end: float | None = None
    title: str


class VideoInfo(BaseModel):
    """The metadata the pipeline needs about one video.

    Only these fields are cached, never yt-dlp's raw output, which is large and holds signed media URLs.

    Attributes:
        id (str): YouTube video ID.
        title (str): Video title.
        channel (str | None): Channel name.
        channel_id (str | None): Channel ID.
        duration (float | None): Length in seconds.
        upload_date (str | None): Upload date as `YYYYMMDD`, a UTC day when YouTube reports an exact time.
        timestamp (float | None): Upload time in Unix seconds. For a stream replay, about when the stream ended.
        release_timestamp (float | None): Release time in Unix seconds, e.g. when a stream started.
        description (str): Video description.
        language (str | None): Language YouTube reports for the video.
        availability (str | None): For example `public`, `unlisted` or `subscriber_only`.
        live_status (str | None): For example `was_live` for livestream replays.
        chapters (list[Chapter]): Uploader-set chapters, possibly empty.
        subtitle_languages (list[str]): Languages of human-made subtitle tracks.
        auto_caption_languages (list[str]): Languages of automatic caption tracks.
    """

    id: str
    title: str
    channel: str | None = None
    channel_id: str | None = None
    duration: float | None = None
    upload_date: str | None = None
    timestamp: float | None = None
    release_timestamp: float | None = None
    description: str = ""
    language: str | None = None
    availability: str | None = None
    live_status: str | None = None
    chapters: list[Chapter] = Field(default_factory=list)
    subtitle_languages: list[str] = Field(default_factory=list)
    auto_caption_languages: list[str] = Field(default_factory=list)

    @property
    def url(self) -> str:
        """The video's watch URL.

        Returns:
            str: The URL.
        """
        return video_url(self.id)

    @property
    def published(self) -> datetime.datetime | None:
        """When the video was published: its release time, else its upload time.

        Returns:
            datetime.datetime | None: The time in UTC, to the second, or None if yt-dlp reported neither.
        """
        seconds = self.release_timestamp if self.release_timestamp is not None else self.timestamp
        if seconds is None:
            return None
        return datetime.datetime.fromtimestamp(seconds, datetime.UTC).replace(microsecond=0)


def video_url(video_id: str) -> str:
    """Build a video's watch URL.

    Args:
        video_id (str): YouTube video ID.

    Returns:
        str: The URL.
    """
    return f"{YOUTUBE_ORIGIN}/watch?v={video_id}"


def parse_video_id(value: str) -> str:
    """Extract the video ID from a video URL, or accept a bare ID.

    Args:
        value (str): A watch, `youtu.be`, Shorts, live or embed URL, or an 11-character video ID.

    Returns:
        str: The video ID.

    Raises:
        ValueError: If no video ID can be found.
    """
    value = value.strip()
    if _VIDEO_ID_PATTERN.match(value):
        return value
    match = _VIDEO_URL_PATTERN.match(value)
    if match is None:
        raise ValueError(f"Not a YouTube video URL or ID: {value!r}")
    return match.group(1)


def video_info_from_ytdlp(data: dict[str, Any]) -> VideoInfo:
    """Pick the needed fields out of yt-dlp's full extraction output (`-J`).

    Args:
        data (dict[str, Any]): yt-dlp's JSON output for one video.

    Returns:
        VideoInfo: The metadata.
    """
    chapters = [
        Chapter(start=chapter["start_time"], end=chapter.get("end_time"), title=chapter.get("title") or "")
        for chapter in data.get("chapters") or []
    ]
    subtitles: dict[str, Any] = data.get("subtitles") or {}
    auto_captions: dict[str, Any] = data.get("automatic_captions") or {}
    return VideoInfo(
        id=data["id"],
        title=data.get("title") or data["id"],
        channel=data.get("channel"),
        channel_id=data.get("channel_id"),
        duration=data.get("duration"),
        upload_date=data.get("upload_date"),
        timestamp=data.get("timestamp"),
        release_timestamp=data.get("release_timestamp"),
        description=data.get("description") or "",
        language=data.get("language"),
        availability=data.get("availability"),
        live_status=data.get("live_status"),
        chapters=chapters,
        subtitle_languages=[language for language in subtitles if language not in _NON_SUBTITLE_TRACKS],
        auto_caption_languages=list(auto_captions),
    )
