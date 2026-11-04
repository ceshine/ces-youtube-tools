"""The `list` command: show a configured channel's latest videos."""

import time
from typing import Annotated

import typer
from rich.table import Table

from .common import fail, CONSOLE, ConfigPathOption, load_config_or_exit
from ..ytdlp import YtDlpError
from ..config import DEFAULT_CONFIG_PATH
from ..youtube import VideoSummary, list_latest_videos
from ..pipeline import make_ytdlp
from ..formatting import format_age, format_duration


def _videos_table(title: str, videos: list[VideoSummary], now: float) -> Table:
    """Build a table of videos for display.

    Args:
        title (str): The table title.
        videos (list[VideoSummary]): The videos, newest first.
        now (float): The current Unix time, for the age column.

    Returns:
        Table: The table.
    """
    table = Table(title=title)
    table.add_column("#", justify="right")
    table.add_column("Published", justify="right")
    table.add_column("Length", justify="right")
    table.add_column("Type")
    table.add_column("Title")
    table.add_column("ID")
    for index, video in enumerate(videos, start=1):
        kinds = (["[magenta]members[/magenta]"] if video.members_only else []) + (
            ["stream"] if video.live_status == "was_live" else []
        )
        table.add_row(
            str(index),
            format_age(video.timestamp, now),
            format_duration(video.duration),
            " ".join(kinds),
            video.title,
            video.id,
        )
    return table


def list_videos(
    channel: Annotated[str, typer.Argument(help="Channel name or ID from the configuration.")],
    limit: Annotated[int, typer.Option("--limit", "-n", min=1, help="Maximum number of videos to show.")] = 20,
    config_path: ConfigPathOption = DEFAULT_CONFIG_PATH,
) -> None:
    """List a configured channel's latest videos and livestream replays, newest first.

    Args:
        channel (str): Channel name (case-insensitive) or channel ID from the configuration.
        limit (int): Maximum number of videos to show.
        config_path (Path): Path of the configuration file.

    Returns:
        None

    Raises:
        typer.Exit: If the channel is not configured or yt-dlp fails.
    """
    config = load_config_or_exit(config_path)
    found = config.find_channel(channel)
    if found is None:
        names = (
            ", ".join(c.name for c in config.channels) or "none, add one with `ces-youtube-tools config add-channel`"
        )
        raise fail(f"No channel named {channel!r} in the configuration. Configured channels: {names}.")
    try:
        with CONSOLE.status(f"Fetching the latest videos of {found.name}..."):
            videos = list_latest_videos(found, make_ytdlp(config, with_cookies=False), limit)
    except YtDlpError as exc:
        raise fail(str(exc)) from exc
    if not videos:
        CONSOLE.print(f"No videos found for {found.name}.")
        return
    CONSOLE.print(_videos_table(f"Latest videos of {found.name}", videos, time.time()))
