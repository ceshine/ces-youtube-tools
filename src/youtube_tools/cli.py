"""Command-line entry point for youtube-tools."""

import time
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table
from rich.console import Console

from .video import parse_video_id
from .ytdlp import YtDlp, YtDlpError
from .config import (
    AppConfig,
    ConfigError,
    dump_config,
    load_config,
    save_config,
    DEFAULT_CONFIG_PATH,
)
from .firefox import (
    is_signed_in,
    FirefoxProfile,
    discover_all_profiles,
    cookies_from_browser_spec,
)
from .youtube import VideoSummary, verify_login, resolve_channel, list_latest_videos
from .pipeline import work_dir_for, load_transcript, load_video_info, make_transcript_provider
from .formatting import format_age, format_duration
from .transcript import TranscriptError

APP = typer.Typer(no_args_is_help=True, help="Browse YouTube channels and generate watching guides.")
CONFIG_APP = typer.Typer(no_args_is_help=True, help="Create and edit the configuration file.")
APP.add_typer(CONFIG_APP, name="config")

CONSOLE = Console()
# Status and headers for commands whose stdout is data meant for piping.
STDERR = Console(stderr=True)

ConfigPathOption = Annotated[Path, typer.Option("--config", help="Path of the configuration file.")]


@APP.callback()
def main() -> None:
    """Browse YouTube channels and generate watching guides.

    The callback keeps the app a command group, so subcommands are invoked by name.

    Returns:
        None
    """


def _fail(message: str) -> typer.Exit:
    """Print an error message and build the exit to raise.

    Args:
        message (str): The error message.

    Returns:
        typer.Exit: An exit with status 1, for the caller to raise.
    """
    STDERR.print(f"[red]Error:[/red] {message}", markup=True, highlight=False)
    return typer.Exit(code=1)


def _load(config_path: Path) -> AppConfig:
    """Load the configuration or exit with a readable error.

    Args:
        config_path (Path): Path of the configuration file.

    Returns:
        AppConfig: The loaded configuration.

    Raises:
        typer.Exit: If the configuration cannot be loaded.
    """
    try:
        return load_config(config_path)
    except ConfigError as exc:
        raise _fail(str(exc)) from exc


def _make_ytdlp(config: AppConfig, *, with_cookies: bool) -> YtDlp:
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


def _pick_profile() -> FirefoxProfile:
    """Show the Firefox profiles and ask the user to pick one.

    Returns:
        FirefoxProfile: The chosen profile.

    Raises:
        typer.Exit: If no Firefox profiles are found.
    """
    profiles = discover_all_profiles(Path.home())
    if not profiles:
        raise _fail("No Firefox profiles found. Create one in Firefox (about:profiles) first.")

    signed_in = [is_signed_in(profile.path) for profile in profiles]
    table = Table(title="Firefox profiles")
    table.add_column("#", justify="right")
    table.add_column("Name")
    table.add_column("Folder")
    table.add_column("YouTube")
    for index, (profile, ok) in enumerate(zip(profiles, signed_in, strict=True), start=1):
        status = "[green]signed in[/green]" if ok else "[dim]not signed in[/dim]"
        table.add_row(str(index), profile.name, str(profile.path), status)
    CONSOLE.print(table)

    default = next((index for index, ok in enumerate(signed_in, start=1) if ok), 1)
    while True:
        choice = typer.prompt("Profile to use for YouTube cookies", default=default, type=int)
        if 1 <= choice <= len(profiles):
            break
        CONSOLE.print(f"Enter a number from 1 to {len(profiles)}.")
    profile = profiles[choice - 1]
    if not signed_in[choice - 1]:
        CONSOLE.print(
            f"[yellow]Warning:[/yellow] {profile.name} is not signed in to YouTube. "
            "Sign in with that profile, then run `youtube-tools config check`."
        )
    return profile


@CONFIG_APP.command("init")
def config_init(
    config_path: ConfigPathOption = DEFAULT_CONFIG_PATH,
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing configuration file.")] = False,
) -> None:
    """Create the configuration file, starting with the Firefox profile to take cookies from.

    Args:
        config_path (Path): Path of the configuration file.
        force (bool): Overwrite an existing configuration file.

    Returns:
        None

    Raises:
        typer.Exit: If the file exists and `force` is not set.
    """
    if config_path.exists() and not force:
        raise _fail(f"{config_path} already exists. Use --force to overwrite it, or `config profile` to change it.")
    profile = _pick_profile()
    config = AppConfig()
    config.auth.firefox_profile = profile.path
    save_config(config, config_path)
    CONSOLE.print(f"Saved {config_path}. Next, add channels with `youtube-tools config add-channel @handle`.")


@CONFIG_APP.command("profile")
def config_profile(config_path: ConfigPathOption = DEFAULT_CONFIG_PATH) -> None:
    """Choose a different Firefox profile for YouTube cookies.

    Args:
        config_path (Path): Path of the configuration file.

    Returns:
        None
    """
    config = _load(config_path)
    profile = _pick_profile()
    config.auth.firefox_profile = profile.path
    save_config(config, config_path)
    CONSOLE.print(f"Using profile {profile.name} ({profile.path}).")


@CONFIG_APP.command("add-channel")
def config_add_channel(
    channel: Annotated[str, typer.Argument(help="Channel handle (@name) or URL.")],
    members: Annotated[bool, typer.Option("--members", help="Also list members-only videos.")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Add without asking for confirmation.")] = False,
    config_path: ConfigPathOption = DEFAULT_CONFIG_PATH,
) -> None:
    """Look up a channel and add it to the frequently used channels.

    Args:
        channel (str): Channel handle or URL.
        members (bool): Also list members-only videos for this channel.
        yes (bool): Add without asking for confirmation.
        config_path (Path): Path of the configuration file.

    Returns:
        None

    Raises:
        typer.Exit: If the channel cannot be resolved or is already in the configuration.
    """
    config = _load(config_path)
    try:
        resolved = resolve_channel(channel, _make_ytdlp(config, with_cookies=False))
    except (ValueError, YtDlpError) as exc:
        raise _fail(str(exc)) from exc
    resolved.members = members

    existing = config.find_channel(resolved.channel_id)
    if existing is not None:
        raise _fail(f"{existing.name} ({existing.channel_id}) is already in the configuration.")

    CONSOLE.print(f"Found [bold]{resolved.name}[/bold] ({resolved.channel_id}) at {resolved.url}", highlight=False)
    if not yes and not typer.confirm("Add this channel?", default=True):
        raise typer.Exit()
    config.channels.append(resolved)
    save_config(config, config_path)
    CONSOLE.print(f"Added {resolved.name}.")


@CONFIG_APP.command("remove-channel")
def config_remove_channel(
    channel: Annotated[str, typer.Argument(help="Channel name or channel ID.")],
    config_path: ConfigPathOption = DEFAULT_CONFIG_PATH,
) -> None:
    """Remove a channel from the frequently used channels.

    Args:
        channel (str): Channel name (case-insensitive) or channel ID.
        config_path (Path): Path of the configuration file.

    Returns:
        None

    Raises:
        typer.Exit: If the channel is not in the configuration.
    """
    config = _load(config_path)
    found = config.find_channel(channel)
    if found is None:
        raise _fail(f"No channel named {channel!r} in the configuration.")
    config.channels.remove(found)
    save_config(config, config_path)
    CONSOLE.print(f"Removed {found.name}.")


@CONFIG_APP.command("show")
def config_show(config_path: ConfigPathOption = DEFAULT_CONFIG_PATH) -> None:
    """Print the configuration and whether the Firefox profile has YouTube sign-in cookies.

    Args:
        config_path (Path): Path of the configuration file.

    Returns:
        None
    """
    config = _load(config_path)
    CONSOLE.print(f"[bold]{config_path}[/bold]")
    CONSOLE.print(dump_config(config), markup=False, highlight=False)
    profile = config.auth.firefox_profile
    if profile is None:
        CONSOLE.print("No Firefox profile configured. Run `youtube-tools config profile`.")
    elif not profile.is_dir():
        CONSOLE.print(f"[red]Firefox profile folder not found:[/red] {profile}")
    else:
        state = "has" if is_signed_in(profile) else "does not have"
        CONSOLE.print(f"The Firefox profile {state} YouTube sign-in cookies.")


@CONFIG_APP.command("check")
def config_check(config_path: ConfigPathOption = DEFAULT_CONFIG_PATH) -> None:
    """Check with YouTube that the configured profile is signed in.

    Args:
        config_path (Path): Path of the configuration file.

    Returns:
        None

    Raises:
        typer.Exit: If no profile is configured or YouTube rejects the cookies.
    """
    config = _load(config_path)
    if config.auth.firefox_profile is None:
        raise _fail("No Firefox profile configured. Run `youtube-tools config profile`.")
    if not verify_login(_make_ytdlp(config, with_cookies=True)):
        raise _fail(
            "YouTube did not accept the cookies. Sign in with the Firefox profile "
            f"{config.auth.firefox_profile}, or choose another with `youtube-tools config profile`."
        )
    CONSOLE.print("[green]Signed in:[/green] YouTube accepted the profile's cookies.")


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


@APP.command("list")
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
    config = _load(config_path)
    found = config.find_channel(channel)
    if found is None:
        names = ", ".join(c.name for c in config.channels) or "none, add one with `youtube-tools config add-channel`"
        raise _fail(f"No channel named {channel!r} in the configuration. Configured channels: {names}.")
    try:
        with CONSOLE.status(f"Fetching the latest videos of {found.name}..."):
            videos = list_latest_videos(found, _make_ytdlp(config, with_cookies=False), limit)
    except YtDlpError as exc:
        raise _fail(str(exc)) from exc
    if not videos:
        CONSOLE.print(f"No videos found for {found.name}.")
        return
    CONSOLE.print(_videos_table(f"Latest videos of {found.name}", videos, time.time()))


@APP.command("transcript")
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
    config = _load(config_path)
    try:
        video_id = parse_video_id(video)
        ytdlp = _make_ytdlp(config, with_cookies=True)  # members-only videos need cookies
        provider = make_transcript_provider(config, ytdlp)
        work_dir = work_dir_for(config.cache_dir, video_id)
        with STDERR.status("Fetching video metadata..."):
            info = load_video_info(video_id, ytdlp, work_dir, refresh=refresh)
        with STDERR.status("Fetching transcript..."):
            transcript = load_transcript(info, provider, work_dir, refresh=refresh)
    except (ValueError, YtDlpError, TranscriptError) as exc:
        raise _fail(str(exc)) from exc

    STDERR.print(f"[bold]{info.title}[/bold] ({info.channel or 'unknown channel'})", highlight=False)
    STDERR.print(
        f"{transcript.source}, {len(transcript.segments)} segments. Cached in {work_dir}", markup=False, highlight=False
    )
    if as_json:
        typer.echo(transcript.model_dump_json(indent=2))
        return
    for segment in transcript.segments:
        typer.echo(f"[{format_duration(segment.start)}] {segment.text}")


if __name__ == "__main__":
    APP()
