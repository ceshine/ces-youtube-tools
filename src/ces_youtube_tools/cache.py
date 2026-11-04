"""Cache each pipeline step's result in the video's work directory.

Work directory layout (`<cache_dir>/<video_id>/`):

- `video.json`: the `VideoInfo` fields (never yt-dlp's raw output, which holds signed media URLs). One without the
  publish time keys was written before they were cached, and is fetched again.
- `subtitles/`: downloaded subtitle files.
- `transcript.json`: the `Transcript`.
- `prompt.md`, `guide.md`: the last guide prompt and the generator's raw reply, for debugging.
"""

import json
import logging
from pathlib import Path

from pydantic import BaseModel, ValidationError

from .video import VideoInfo, video_url, video_info_from_ytdlp
from .ytdlp import YtDlp
from .transcript import Transcript, TranscriptProvider

LOGGER = logging.getLogger(__name__)

# A `video.json` without these keys was written before they were cached, so it is stale. A key that is present with
# `null` means yt-dlp did not report the value.
_VIDEO_CACHE_KEYS = frozenset({"timestamp", "release_timestamp"})


class CacheError(Exception):
    """Raised when a work directory or a file in it cannot be written."""


def work_dir_for(cache_dir: Path, video_id: str) -> Path:
    """Get a video's work directory, creating it if needed.

    Args:
        cache_dir (Path): Root of the work directories.
        video_id (str): YouTube video ID.

    Returns:
        Path: The work directory.

    Raises:
        CacheError: If the directory cannot be created.
    """
    work_dir = cache_dir / video_id
    try:
        work_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise CacheError(
            f"Cannot create the work directory {work_dir} ({exc}). Check cache_dir in the configuration."
        ) from exc
    return work_dir


def _load_cached[ModelT: BaseModel](
    path: Path, model: type[ModelT], required_keys: frozenset[str] = frozenset()
) -> ModelT | None:
    """Load a cached model, ignoring a missing, unreadable or stale file.

    Args:
        path (Path): The cache file.
        model (type[ModelT]): The model class to validate the file with.
        required_keys (frozenset[str]): Keys the file's JSON object must have. A file without one of them was written
            by an older version and is stale, even though it validates (the fields have defaults).

    Returns:
        ModelT | None: The cached value, or None if the file is missing, unreadable, invalid or stale (it is then
            rebuilt).
    """
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
        cached = model.model_validate_json(text)
    except (OSError, UnicodeDecodeError, ValidationError) as exc:
        LOGGER.warning("Ignoring invalid cache file %s: %s", path, exc)
        return None
    if required_keys:
        # The text validated as a model, so it is a JSON object.
        missing = required_keys - json.loads(text).keys()
        if missing:
            LOGGER.info("Ignoring cache file %s, written before it held %s", path, ", ".join(sorted(missing)))
            return None
    return cached


def write_cache_text(path: Path, text: str) -> None:
    """Write a text file in a work directory.

    Args:
        path (Path): The file.
        text (str): Its content.

    Returns:
        None

    Raises:
        CacheError: If the file cannot be written.
    """
    try:
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise CacheError(f"Cannot write the cache file {path} ({exc}).") from exc


def _save(path: Path, value: BaseModel) -> None:
    """Write a model to a cache file.

    Args:
        path (Path): The cache file.
        value (BaseModel): The value to write.

    Returns:
        None

    Raises:
        CacheError: If the file cannot be written.
    """
    write_cache_text(path, value.model_dump_json(indent=2))


def load_video_info(video_id: str, ytdlp: YtDlp, work_dir: Path, refresh: bool = False) -> VideoInfo:
    """Get a video's metadata, from the cache or with a full yt-dlp extraction.

    Args:
        video_id (str): YouTube video ID.
        ytdlp (YtDlp): The yt-dlp runner, with cookies for members-only videos.
        work_dir (Path): The video's work directory.
        refresh (bool): Ignore the cache and extract again.

    Returns:
        VideoInfo: The metadata.

    Raises:
        YtDlpError: If yt-dlp fails, e.g. for a members-only video without a membership.
        CacheError: If the metadata cannot be saved in the work directory.
    """
    cache_file = work_dir / "video.json"
    if not refresh and (cached := _load_cached(cache_file, VideoInfo, _VIDEO_CACHE_KEYS)) is not None:
        return cached
    video = video_info_from_ytdlp(ytdlp.run_json(["--skip-download", video_url(video_id)]))
    _save(cache_file, video)
    return video


def load_transcript(
    video: VideoInfo, provider: TranscriptProvider, work_dir: Path, refresh: bool = False
) -> Transcript:
    """Get a video's transcript, from the cache or from the provider.

    Args:
        video (VideoInfo): The video.
        provider (TranscriptProvider): The transcript provider.
        work_dir (Path): The video's work directory.
        refresh (bool): Ignore the cache and fetch again.

    Returns:
        Transcript: The transcript.

    Raises:
        TranscriptError: If the provider cannot produce a transcript.
        YtDlpError: If yt-dlp fails.
        CacheError: If the transcript cannot be saved in the work directory.
    """
    cache_file = work_dir / "transcript.json"
    if not refresh and (cached := _load_cached(cache_file, Transcript)) is not None:
        return cached
    transcript = provider.fetch(video, work_dir)
    _save(cache_file, transcript)
    return transcript
