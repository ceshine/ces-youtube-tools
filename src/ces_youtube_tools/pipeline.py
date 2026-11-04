"""Run the pipeline steps for one video: build the transcript provider, and write the guide.

Each step's result is cached in the video's work directory (see `cache`).
"""

import datetime
from pathlib import Path
from dataclasses import dataclass
from collections.abc import Callable

from .cache import CacheError, write_cache_text
from .guide import (
    LinkCheck,
    GuideError,
    clean_output,
    render_prompt,
    guide_filename,
    default_template,
    check_timestamp_links,
)
from .video import VideoInfo
from .ytdlp import YtDlp
from .config import AppConfig
from .subtitles import PROVIDER_NAME as SUBTITLES_PROVIDER, YtDlpSubtitleProvider
from .generators.base import GuideGenerator
from .transcript import Transcript, TranscriptProvider
from .front_matter import metadata_from_video, render_front_matter
from .existing_guides import GuideScan, is_same_file, check_existing_guides

TranscriptProviderFactory = Callable[[AppConfig, YtDlp], TranscriptProvider]

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
    write_cache_text(work_dir / "prompt.md", prompt)
    reply = generator.generate(prompt, video, work_dir)
    raw_reply_path = work_dir / "guide.md"
    # The generation is already paid for, so a failed save of the raw reply must not stop the guide being written.
    raw_reply_error: str | None = None
    try:
        write_cache_text(raw_reply_path, reply)
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
