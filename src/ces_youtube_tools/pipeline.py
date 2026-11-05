"""Run the pipeline steps for one video: build the tools, load its metadata and transcript, and write the guide.

Each step's result is cached in the video's work directory (see `cache`). Nothing here prints: a step that takes a
while shows a status message through the `status` argument, and notices are returned as data for the caller to print.
"""

import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from contextlib import nullcontext, AbstractContextManager
from dataclasses import dataclass
from collections.abc import Callable

from .cache import CacheError, work_dir_for, load_transcript, load_video_info, write_cache_text
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
from .ytdlp import YtDlp, YtDlpError
from .config import AppConfig
from .firefox import cookies_from_browser_spec
from .subtitles import PROVIDER_NAME as SUBTITLES_PROVIDER, YtDlpSubtitleProvider
from .generators import make_guide_generator
from .generators.base import GuideGenerator
from .transcript import Transcript, TranscriptError, TranscriptProvider
from .front_matter import metadata_from_video, render_front_matter
from .existing_guides import GuideScan, is_same_file, check_existing_guides, find_guides_for_video

# Shows a status message, e.g. a spinner, while the `with` block runs. The CLI passes `Console.status`.
StatusFactory = Callable[[str], AbstractContextManager[object]]


def _no_status(_message: str) -> AbstractContextManager[object]:
    """Show nothing: the default `status` of the pipeline steps.

    Args:
        _message (str): The status message, ignored.

    Returns:
        AbstractContextManager[object]: A context manager that does nothing.
    """
    return nullcontext()


def make_ytdlp(config: AppConfig, *, with_cookies: bool) -> YtDlp:
    """Build a yt-dlp runner, optionally using the configured Firefox profile's cookies.

    Cookies are only needed to download members-only content. Leave them out otherwise, to keep automated traffic
    tied to the account low.

    Args:
        config (AppConfig): The configuration.
        with_cookies (bool): Pass the profile's cookies to yt-dlp.

    Returns:
        YtDlp: The runner. It runs without cookies when no profile is configured.
    """
    profile = config.auth.firefox_profile
    cookies_spec = cookies_from_browser_spec(profile) if with_cookies and profile else None
    return YtDlp(config.ytdlp_command, cookies_spec)


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
    """Generate a guide and write it to `output_dir`, with front matter: the video's metadata and how it was written.

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
    metadata = metadata_from_video(video, now).model_copy(
        update={"harness": generator.harness, "model": generator.model, "effort": generator.effort}
    )
    front_matter = render_front_matter(metadata)
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


# The errors the guide steps raise for the caller to report as a message, without a traceback. It must cover the
# `Raises:` sections of `make_guide_tools`, `prepare_guide` and `generate_guide`.
GUIDE_ERRORS = (ValueError, YtDlpError, TranscriptError, GuideError, CacheError)


@dataclass(frozen=True)
class GuideTools:
    """What writing guides needs, made once per command run.

    Attributes:
        ytdlp (YtDlp): The yt-dlp runner, with cookies because members-only videos need them.
        provider (TranscriptProvider): The configured transcript provider.
        generator (GuideGenerator): The configured guide generator.
        generator_name (str): The generator's name in `guide.generator`, for status messages.
        template (str): The prompt template.
        template_is_default (bool): True if `template` is the built-in one, because the configured file does not
            exist.
        timezone (ZoneInfo | None): The time zone for the date in guide file names, or None for the system's.
        cache_dir (Path): The directory that holds each video's work directory.
    """

    ytdlp: YtDlp
    provider: TranscriptProvider
    generator: GuideGenerator
    generator_name: str
    template: str
    template_is_default: bool
    timezone: ZoneInfo | None
    cache_dir: Path


@dataclass(frozen=True)
class PreparedGuide:
    """A video whose metadata is loaded, with where its guide goes and the files already about it.

    Attributes:
        info (VideoInfo): The video's metadata.
        work_dir (Path): The video's cache directory.
        scan (GuideScan): The files already about the video in the guide's folder. Its `warnings` name the files
            that could not be read or whose front matter is invalid.
        target (Path): Where the guide goes: always `target_dir / guide_filename(info, timezone)`, a bare file name
            in the guide's folder, so `target.parent` is that folder.
    """

    info: VideoInfo
    work_dir: Path
    scan: GuideScan
    target: Path


def make_guide_tools(config: AppConfig) -> GuideTools:
    """Make the yt-dlp runner, transcript provider, generator and prompt template for writing guides.

    Args:
        config (AppConfig): The configuration.

    Returns:
        GuideTools: The tools. `template_is_default` says whether the built-in prompt template is used.

    Raises:
        ValueError: If the configured transcript provider or guide generator is unknown, or the generator's settings
            are invalid.
    """
    ytdlp = make_ytdlp(config, with_cookies=True)  # members-only videos need cookies
    provider = make_transcript_provider(config, ytdlp)
    generator = make_guide_generator(config)
    template, is_default = load_prompt_template(config.guide.prompt_template)
    return GuideTools(
        ytdlp=ytdlp,
        provider=provider,
        generator=generator,
        generator_name=config.guide.generator,
        template=template,
        template_is_default=is_default,
        timezone=config.guide.file_name_zone(),
        cache_dir=config.cache_dir,
    )


def prepare_guide(
    tools: GuideTools, video_id: str, target_dir: Path, *, refresh: bool, status: StatusFactory = _no_status
) -> PreparedGuide:
    """Load a video's metadata, work out where its guide goes, and find the files already about the video.

    Args:
        tools (GuideTools): The tools from `make_guide_tools`.
        video_id (str): YouTube video ID.
        target_dir (Path): Directory for the guide file.
        refresh (bool): Ignore cached metadata.
        status (StatusFactory): Shows a status message while the metadata is fetched. The default shows nothing.

    Returns:
        PreparedGuide: The video and its guide's path. The caller reports `scan.warnings`.

    Raises:
        CacheError: If the video's cache directory cannot be created, or its metadata cannot be saved there.
        YtDlpError: If yt-dlp fails, e.g. for a members-only video without a membership.
    """
    work_dir = work_dir_for(tools.cache_dir, video_id)
    with status("Fetching video metadata..."):
        info = load_video_info(video_id, tools.ytdlp, work_dir, refresh=refresh)
    scan = find_guides_for_video(target_dir, info.id)
    return PreparedGuide(info, work_dir, scan, target_dir / guide_filename(info, tools.timezone))


def generate_guide(
    tools: GuideTools,
    prepared: PreparedGuide,
    *,
    force: bool,
    refresh: bool,
    status: StatusFactory = _no_status,
) -> GuideResult:
    """Fetch a prepared video's transcript, generate its guide and write it.

    Args:
        tools (GuideTools): The tools from `make_guide_tools`.
        prepared (PreparedGuide): The video, from `prepare_guide`.
        force (bool): Replace an existing guide for the video, and overwrite a file with the guide's name.
        refresh (bool): Ignore the cached transcript.
        status (StatusFactory): Shows a status message while the transcript is fetched and the guide written. The
            default shows nothing.

    Returns:
        GuideResult: The written guide.

    Raises:
        GuideError: If an existing guide is in the way and `force` is not set, a file is about the video only by its
            links, or generating or writing the guide fails.
        TranscriptError: If the provider cannot produce a transcript.
        YtDlpError: If yt-dlp fails while fetching the transcript.
        CacheError: If the transcript or the prompt cannot be saved in the cache.
        ValueError: If the prepared scan is for another video.
    """
    # Refuse before fetching the transcript; `write_guide` checks again.
    _ = check_existing_guides(prepared.target, prepared.scan, force)
    with status("Fetching transcript..."):
        transcript = load_transcript(prepared.info, tools.provider, prepared.work_dir, refresh=refresh)
    with status(f"Writing the guide with {tools.generator_name} (this can take a few minutes)..."):
        return write_guide(
            prepared.info,
            transcript,
            tools.template,
            tools.generator,
            prepared.work_dir,
            prepared.target.parent,
            datetime.datetime.now().astimezone(),
            timezone=tools.timezone,
            existing=prepared.scan,
            force=force,
        )
