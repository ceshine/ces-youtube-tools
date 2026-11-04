"""The `guide` command: generate a watching guide for one video."""

from pathlib import Path
from typing import Annotated

import typer

from .common import fail, STDERR, ConfigPathOption, load_config_or_exit
from .guide_report import report_guide, print_scan_warnings, make_guide_tools_with_notice
from ..video import parse_video_id
from ..config import AppConfig, DEFAULT_CONFIG_PATH
from ..pipeline import GUIDE_ERRORS, prepare_guide, generate_guide


def make_guide(
    video: Annotated[str, typer.Argument(help="Video URL or ID.")],
    output_dir: Annotated[
        Path | None, typer.Option("--output-dir", "-o", help="Where to write the guide. Overrides the config.")
    ] = None,
    force: Annotated[
        bool, typer.Option("--force", help="Replace an existing guide for the video, whatever its name.")
    ] = False,
    refresh: Annotated[bool, typer.Option("--refresh", help="Ignore cached metadata and transcript.")] = False,
    config_path: ConfigPathOption = DEFAULT_CONFIG_PATH,
) -> None:
    """Generate a watching guide for a video and write it as Markdown.

    The guide goes to `--output-dir`, else the configured `guide.output_dir`, else the current directory. A guide for
    the same video already in that folder, under any name, is refused unless `--force` is given; it is then replaced.

    Args:
        video (str): Video URL or ID.
        output_dir (Path | None): Directory for the guide file.
        force (bool): Replace an existing guide for the video, and overwrite a file with the guide's name.
        refresh (bool): Ignore cached metadata and transcript.
        config_path (Path): Path of the configuration file.

    Returns:
        None

    Raises:
        typer.Exit: If any step fails.
    """
    config = load_config_or_exit(config_path)
    try:
        video_id = parse_video_id(video)
    except ValueError as exc:
        raise fail(str(exc)) from exc
    _run_guide(config, video_id, output_dir or config.guide.output_dir or Path.cwd(), force=force, refresh=refresh)


def _run_guide(config: AppConfig, video_id: str, target_dir: Path, *, force: bool, refresh: bool) -> None:
    """Fetch a video's transcript, generate its guide, write it and report the result.

    Args:
        config (AppConfig): The configuration.
        video_id (str): YouTube video ID.
        target_dir (Path): Directory for the guide file.
        force (bool): Replace an existing guide for the video, and overwrite a file with the guide's name.
        refresh (bool): Ignore cached metadata and transcript.

    Returns:
        None

    Raises:
        typer.Exit: If any step fails.
    """
    try:
        tools = make_guide_tools_with_notice(config)
        prepared = prepare_guide(tools, video_id, target_dir, refresh=refresh, status=STDERR.status)
        print_scan_warnings(prepared)
        result = generate_guide(tools, prepared, force=force, refresh=refresh, status=STDERR.status)
    except GUIDE_ERRORS as exc:
        raise fail(str(exc)) from exc
    report_guide(result)
