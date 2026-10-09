"""Run the pipeline steps for one video, caching each step's result in the video's work directory.

Work directory layout (`<cache_dir>/<video_id>/`):

- `video.json`: the `VideoInfo` fields (never yt-dlp's raw output, which holds signed media URLs).
- `subtitles/`: downloaded subtitle files.
- `transcript.json`: the `Transcript`.
- `prompt.md`, `guide.md`: the last guide prompt and the generator's raw reply, for debugging.
"""

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
from .transcript import Transcript, TranscriptProvider

LOGGER = logging.getLogger(__name__)

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


def work_dir_for(cache_dir: Path, video_id: str) -> Path:
    """Get a video's work directory, creating it if needed.

    Args:
        cache_dir (Path): Root of the work directories.
        video_id (str): YouTube video ID.

    Returns:
        Path: The work directory.
    """
    work_dir = cache_dir / video_id
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir


def _load_cached[ModelT: BaseModel](path: Path, model: type[ModelT]) -> ModelT | None:
    """Load a cached model, ignoring a missing or unreadable file.

    Args:
        path (Path): The cache file.
        model (type[ModelT]): The model class to validate the file with.

    Returns:
        ModelT | None: The cached value, or None if the file is missing or invalid (it is then rebuilt).
    """
    if not path.exists():
        return None
    try:
        return model.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as exc:
        LOGGER.warning("Ignoring invalid cache file %s: %s", path, exc)
        return None


def _save(path: Path, value: BaseModel) -> None:
    """Write a model to a cache file.

    Args:
        path (Path): The cache file.
        value (BaseModel): The value to write.

    Returns:
        None
    """
    path.write_text(value.model_dump_json(indent=2), encoding="utf-8")


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
    """
    cache_file = work_dir / "video.json"
    if not refresh and (cached := _load_cached(cache_file, VideoInfo)) is not None:
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
class GuideResult:
    """A written guide.

    Attributes:
        path (Path): Where the guide was written.
        links (LinkCheck): What the timestamp link check found and fixed.
    """

    path: Path
    links: LinkCheck


def write_guide(
    video: VideoInfo,
    transcript: Transcript,
    template: str,
    generator: GuideGenerator,
    work_dir: Path,
    output_dir: Path,
    today: datetime.date,
    force: bool = False,
) -> GuideResult:
    """Generate a guide and write it to `output_dir`.

    The output path is checked before generating, so an existing guide does not cost a generation. The prompt and
    the raw reply are kept in the work directory.

    Args:
        video (VideoInfo): The video.
        transcript (Transcript): Its transcript.
        template (str): The prompt template.
        generator (GuideGenerator): The guide generator.
        work_dir (Path): The video's work directory.
        output_dir (Path): Directory for the guide file. Created if needed.
        today (datetime.date): The date the guide is written.
        force (bool): Overwrite an existing guide file.

    Returns:
        GuideResult: The guide's path and the link check result.

    Raises:
        GuideError: If the guide file exists and `force` is not set, the template is invalid, or generation fails.
    """
    path = output_dir / guide_filename(video)
    if path.exists() and not force:
        raise GuideError(f"{path} already exists. Use --force to overwrite it.")
    prompt = render_prompt(template, video, transcript, today)
    (work_dir / "prompt.md").write_text(prompt, encoding="utf-8")
    reply = generator.generate(prompt, video, work_dir)
    (work_dir / "guide.md").write_text(reply, encoding="utf-8")
    links = check_timestamp_links(clean_output(reply), video)
    output_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(links.markdown, encoding="utf-8")
    return GuideResult(path=path, links=links)
