"""The `transcript` command: fetch a video's transcript and print it."""

from typing import Annotated

import typer

from .common import fail, STDERR, ConfigPathOption, load_config_or_exit
from ..cache import CacheError, work_dir_for, load_transcript, load_video_info
from ..video import parse_video_id
from ..ytdlp import YtDlpError
from ..config import DEFAULT_CONFIG_PATH
from ..pipeline import make_ytdlp, make_transcript_provider
from ..formatting import format_duration
from ..transcript import TranscriptError


def show_transcript(
    video: Annotated[str, typer.Argument(help="Video URL or ID.")],
    refresh: Annotated[bool, typer.Option("--refresh", help="Ignore cached metadata and transcript.")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Print the transcript as JSON.")] = False,
    config_path: ConfigPathOption = DEFAULT_CONFIG_PATH,
) -> None:
    """Fetch a video's transcript and print it to stdout, with timestamps.

    Results are cached in the video's work directory under `cache_dir`.

    Args:
        video (str): Video URL or ID.
        refresh (bool): Ignore cached metadata and transcript.
        as_json (bool): Print the transcript as JSON instead of timestamped lines.
        config_path (Path): Path of the configuration file.

    Returns:
        None

    Raises:
        typer.Exit: If the video ID is invalid or no transcript can be fetched.
    """
    config = load_config_or_exit(config_path)
    try:
        video_id = parse_video_id(video)
        ytdlp = make_ytdlp(config, with_cookies=True)  # members-only videos need cookies
        provider = make_transcript_provider(config, ytdlp)
        work_dir = work_dir_for(config.cache_dir, video_id)
        with STDERR.status("Fetching video metadata..."):
            info = load_video_info(video_id, ytdlp, work_dir, refresh=refresh)
        with STDERR.status("Fetching transcript..."):
            transcript = load_transcript(info, provider, work_dir, refresh=refresh)
    except (ValueError, YtDlpError, TranscriptError, CacheError) as exc:
        raise fail(str(exc)) from exc

    STDERR.print(f"[bold]{info.title}[/bold] ({info.channel or 'unknown channel'})", highlight=False)
    STDERR.print(
        f"{transcript.source}, {len(transcript.segments)} segments. Cached in {work_dir}", markup=False, highlight=False
    )
    if as_json:
        typer.echo(transcript.model_dump_json(indent=2))
        return
    for segment in transcript.segments:
        typer.echo(f"[{format_duration(segment.start)}] {segment.text}")
