"""Run the pipeline steps for one video, caching each step's result in the video's work directory.

Work directory layout (`<cache_dir>/<video_id>/`):

- `video.json`: the `VideoInfo` fields (never yt-dlp's raw output, which holds signed media URLs). One without the
  publish time keys was written before they were cached, and is fetched again.
- `subtitles/`: downloaded subtitle files.
- `transcript.json`: the `Transcript`.
- `prompt.md`, `guide.md`: the last guide prompt and the generator's raw reply, for debugging.
"""

import json
import logging
import datetime
from pathlib import Path
from dataclasses import dataclass
from collections.abc import Callable

from pydantic import BaseModel, ValidationError

from .guide import (
    LinkCheck,
    GuideError,
    clean_output,
    render_prompt,
    GuideGenerator,
    guide_filename,
    default_template,
    check_timestamp_links,
)
from .video import VideoInfo, video_url, video_info_from_ytdlp
from .ytdlp import YtDlp
from .config import AppConfig
from .subtitles import PROVIDER_NAME as SUBTITLES_PROVIDER, YtDlpSubtitleProvider
from .claude_cli import GENERATOR_NAME as CLAUDE_CLI_GENERATOR, ClaudeCliOptions, ClaudeCliGenerator
from .guide_html import is_markdown, find_video_id
from .transcript import Transcript, TranscriptProvider
from .front_matter import FrontMatterError, split_front_matter, metadata_from_video, render_front_matter

LOGGER = logging.getLogger(__name__)

TranscriptProviderFactory = Callable[[AppConfig, YtDlp], TranscriptProvider]

# A `video.json` without these keys was written before they were cached, so it is stale. A key that is present with
# `null` means yt-dlp did not report the value.
_VIDEO_CACHE_KEYS = frozenset({"timestamp", "release_timestamp"})

TRANSCRIPT_PROVIDERS: dict[str, TranscriptProviderFactory] = {
    SUBTITLES_PROVIDER: lambda config, ytdlp: YtDlpSubtitleProvider(ytdlp, config.transcript.languages),
}


def make_transcript_provider(config: AppConfig, ytdlp: YtDlp) -> TranscriptProvider:
    """Build the transcript provider named in the configuration.

    Args:
        config (AppConfig): The configuration.
        ytdlp (YtDlp): The yt-dlp runner, with cookies for members-only videos.

    Returns:
        TranscriptProvider: The provider.

    Raises:
        ValueError: If the configured provider name is unknown.
    """
    name = config.transcript.provider
    factory = TRANSCRIPT_PROVIDERS.get(name)
    if factory is None:
        raise ValueError(f"Unknown transcript provider {name!r}. Available: {', '.join(sorted(TRANSCRIPT_PROVIDERS))}.")
    return factory(config, ytdlp)


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


def _write_cache_text(path: Path, text: str) -> None:
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
    _write_cache_text(path, value.model_dump_json(indent=2))


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


GuideGeneratorFactory = Callable[[AppConfig], GuideGenerator]


def _make_claude_cli(config: AppConfig) -> GuideGenerator:
    """Build the `claude` CLI generator from its settings table.

    Args:
        config (AppConfig): The configuration.

    Returns:
        GuideGenerator: The generator.
    """
    return ClaudeCliGenerator(ClaudeCliOptions.model_validate(config.guide.options.get(CLAUDE_CLI_GENERATOR, {})))


GUIDE_GENERATORS: dict[str, GuideGeneratorFactory] = {CLAUDE_CLI_GENERATOR: _make_claude_cli}


def make_guide_generator(config: AppConfig) -> GuideGenerator:
    """Build the guide generator named in the configuration.

    Args:
        config (AppConfig): The configuration.

    Returns:
        GuideGenerator: The generator.

    Raises:
        ValueError: If the generator name is unknown or its `[guide.options.<name>]` settings are invalid.
    """
    name = config.guide.generator
    factory = GUIDE_GENERATORS.get(name)
    if factory is None:
        raise ValueError(f"Unknown guide generator {name!r}. Available: {', '.join(sorted(GUIDE_GENERATORS))}.")
    try:
        return factory(config)
    except ValidationError as exc:
        raise ValueError(f"Invalid settings in [guide.options.{name}]:\n{exc}") from exc


def load_prompt_template(path: Path) -> tuple[str, bool]:
    """Read the prompt template, falling back to the built-in one.

    Args:
        path (Path): The configured template file.

    Returns:
        tuple[str, bool]: The template text, and True if the built-in template was used because the file is missing.
    """
    if path.exists():
        return path.read_text(encoding="utf-8"), False
    return default_template(), True


@dataclass(frozen=True)
class GuideScan:
    """The files already about one video in a folder.

    Attributes:
        video_id (str): The video.
        paths (list[Path]): Guides whose front matter names the video, in name order. Only these may be replaced.
        linked (list[Path]): Files about the video only by their links (no front matter `video_id`, or invalid front
            matter), in name order. They may not be guides, so they are never replaced.
        warnings (list[str]): Files that could not be read (skipped), or whose front matter is invalid (judged by
            their links).
    """

    video_id: str
    paths: list[Path]
    linked: list[Path]
    warnings: list[str]


def find_guides_for_video(output_dir: Path, video_id: str) -> GuideScan:
    """Find the files about a video among the `.md` files in a folder, whatever their names.

    A file's video is its front matter's `video_id`, else the video its links point to most, as in `site`. A file
    that cannot be read never stops a guide from being written; it is only reported.

    Args:
        output_dir (Path): The folder. One that does not exist holds no guides.
        video_id (str): YouTube video ID.

    Returns:
        GuideScan: The files about the video, by how they were found, and the files that could not be checked
            properly.
    """
    paths: list[Path] = []
    linked: list[Path] = []
    warnings: list[str] = []
    if not output_dir.is_dir():
        return GuideScan(video_id=video_id, paths=paths, linked=linked, warnings=warnings)
    try:
        files = sorted(child for child in output_dir.iterdir() if is_markdown(child) and child.is_file())
    except OSError as exc:
        warnings.append(f"Cannot list {output_dir} ({exc}), so it was not checked for guides.")
        return GuideScan(video_id=video_id, paths=paths, linked=linked, warnings=warnings)
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            warnings.append(f"Cannot read {path} ({exc}), so it was not checked for a guide of this video.")
            continue
        try:
            metadata, markdown = split_front_matter(text)
        except FrontMatterError as exc:
            warnings.append(f"{path}: {exc}. Its links were used to find its video.")
            metadata, markdown = None, text
        if metadata is not None and metadata.video_id is not None:
            if metadata.video_id == video_id:
                paths.append(path)
        elif find_video_id(markdown) == video_id:
            linked.append(path)
    return GuideScan(video_id=video_id, paths=paths, linked=linked, warnings=warnings)


def is_same_file(first: Path, second: Path) -> bool:
    """Check whether two paths name one file, as through a symbolic link or a case-insensitive file system.

    Args:
        first (Path): A path.
        second (Path): Another path.

    Returns:
        bool: True if both exist and are the same file.
    """
    try:
        return first.samefile(second)
    except OSError:
        return False


def guides_to_replace(path: Path, scan: GuideScan) -> list[Path]:
    """List the files a new guide at `path` would replace.

    Args:
        path (Path): Where the new guide goes.
        scan (GuideScan): The files about the video in its folder, from `find_guides_for_video`.

    Returns:
        list[Path]: The file at `path` if it exists, then the older guides of the video under other names.

    Raises:
        GuideError: If a file is about the video only by its links. It may not be a guide, so it is never replaced,
            even with `--force`.
    """
    linked = [old for old in scan.linked if not is_same_file(old, path)]
    if linked:
        names = ", ".join(str(old) for old in linked)
        raise GuideError(
            f"{names} links to this video, but its front matter does not name it, so it is not replaced, even with "
            "--force. Remove or move it, or add `video_id` to its front matter, then run again."
        )
    others = [old for old in scan.paths if old != path and not is_same_file(old, path)]
    return ([path] if path.exists() else []) + others


def check_existing_guides(path: Path, scan: GuideScan, force: bool) -> list[Path]:
    """Refuse to write a guide over an existing one, unless forced.

    Args:
        path (Path): Where the new guide goes.
        scan (GuideScan): The files about the video in its folder, from `find_guides_for_video`.
        force (bool): Allow overwriting the file at `path` and replacing the older guides.

    Returns:
        list[Path]: The older guides under other names, to remove once the new guide is written.

    Raises:
        GuideError: If the file at `path` exists or the video has older guides and `force` is not set, or a file is
            about the video only by its links.
    """
    replaced = guides_to_replace(path, scan)
    if path.exists() and not force:
        raise GuideError(f"{path} already exists. Use --force to overwrite it.")
    others = [old for old in replaced if old != path]
    if others and not force:
        names = ", ".join(str(old) for old in others)
        raise GuideError(f"Video {scan.video_id} already has a guide: {names}. Use --force to replace it.")
    return others


@dataclass(frozen=True)
class GuideResult:
    """A written guide.

    Attributes:
        path (Path): Where the guide was written.
        links (LinkCheck): What the timestamp link check found and fixed.
        removed (list[Path]): Older guides for the same video, under other names, that were removed.
        raw_reply_error (str | None): Why the raw reply could not be saved in the work directory, or None if it was.
    """

    path: Path
    links: LinkCheck
    removed: list[Path]
    raw_reply_error: str | None = None


def write_guide(
    video: VideoInfo,
    transcript: Transcript,
    template: str,
    generator: GuideGenerator,
    work_dir: Path,
    output_dir: Path,
    now: datetime.datetime,
    *,
    timezone: datetime.tzinfo | None = None,
    existing: GuideScan | None = None,
    force: bool = False,
) -> GuideResult:
    """Generate a guide and write it to `output_dir`, with the video's metadata as front matter.

    The output path and the older guides are checked before generating, so an existing guide does not cost a
    generation. The prompt and the raw reply are kept in the work directory. A raw reply that cannot be saved does not
    stop the guide from being written; `GuideResult.raw_reply_error` says why.

    Args:
        video (VideoInfo): The video.
        transcript (Transcript): Its transcript.
        template (str): The prompt template.
        generator (GuideGenerator): The guide generator.
        work_dir (Path): The video's work directory.
        output_dir (Path): Directory for the guide file. Created if needed.
        now (datetime.datetime): When the guide is written, time zone aware. `$today` is its date in its own time
            zone, and the front matter's `generated` is the time in UTC.
        timezone (datetime.tzinfo | None): Time zone of the publish day in the file name and `$upload_date`. None
            means the system's time zone.
        existing (GuideScan | None): The files about the video in `output_dir`, from `find_guides_for_video`. None
            means there are none.
        force (bool): Overwrite an existing guide file, and remove the older guides under other names after writing.
            Files about the video only by their links are refused even then.

    Returns:
        GuideResult: The guide's path, the link check result, the older guides removed, and whether the raw reply was
            saved.

    Raises:
        GuideError: If `check_existing_guides` refuses, the template is invalid, the output directory cannot be
            created, generation fails, the guide file cannot be written, or an older guide cannot be removed after
            writing.
        CacheError: If the prompt cannot be saved in the work directory. This is checked before generating.
        ValueError: If `existing` is a scan for another video.
    """
    # A scan for another video would refuse with the wrong files, and with `force` remove that video's guides.
    if existing is not None and existing.video_id != video.id:
        raise ValueError(f"The guide scan is for video {existing.video_id}, not {video.id}.")
    path = output_dir / guide_filename(video, timezone)
    scan = existing if existing is not None else GuideScan(video_id=video.id, paths=[], linked=[], warnings=[])
    others = check_existing_guides(path, scan, force)
    prompt = render_prompt(template, video, transcript, now, timezone)
    # Create the directory before generating, so a bad output directory does not cost a generation.
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise GuideError(f"Cannot create the output directory {output_dir} ({exc}).") from exc
    _write_cache_text(work_dir / "prompt.md", prompt)
    reply = generator.generate(prompt, video, work_dir)
    raw_reply_path = work_dir / "guide.md"
    # The generation is already paid for, so a failed save of the raw reply must not stop the guide being written.
    raw_reply_error: str | None = None
    try:
        _write_cache_text(raw_reply_path, reply)
    except CacheError as exc:
        raw_reply_error = str(exc)
    links = check_timestamp_links(clean_output(reply), video)
    front_matter = render_front_matter(metadata_from_video(video, now))
    try:
        path.write_text(front_matter + links.markdown, encoding="utf-8")
    except OSError as exc:
        if raw_reply_error is None:
            fallback = f"The generator's raw reply is kept in {raw_reply_path}."
        else:
            fallback = f"The raw reply could not be saved either, so the generation is lost: {raw_reply_error}"
        raise GuideError(f"Cannot write {path} ({exc}). {fallback}") from exc
    removed: list[Path] = []
    for old in others:
        # Checked again now that the new guide exists: removing another name of it would lose the generation.
        if is_same_file(old, path):
            continue
        try:
            old.unlink(missing_ok=True)
        except OSError as exc:
            raise GuideError(
                f"Wrote {path}, but cannot remove the older guide {old} ({exc}). Remove it by hand: `site` refuses two "
                "guides for one video."
            ) from exc
        removed.append(old)
    return GuideResult(path=path, links=links, removed=removed, raw_reply_error=raw_reply_error)
