"""Command-line entry point for youtube-tools."""

from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table
from rich.console import Console

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
from .youtube import verify_login, resolve_channel

APP = typer.Typer(no_args_is_help=True, help="Browse YouTube channels and generate watching guides.")
CONFIG_APP = typer.Typer(no_args_is_help=True, help="Create and edit the configuration file.")
APP.add_typer(CONFIG_APP, name="config")

CONSOLE = Console()

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
    CONSOLE.print(f"[red]Error:[/red] {message}", markup=True, highlight=False)
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


def _make_ytdlp(config: AppConfig) -> YtDlp:
    """Build a yt-dlp runner that uses the configured Firefox profile's cookies.

    Args:
        config (AppConfig): The configuration.

    Returns:
        YtDlp: The runner. It runs without cookies when no profile is configured.
    """
    profile = config.auth.firefox_profile
    return YtDlp(config.ytdlp_command, cookies_from_browser_spec(profile) if profile else None)


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
        resolved = resolve_channel(channel, _make_ytdlp(config))
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
    if not verify_login(_make_ytdlp(config)):
        raise _fail(
            "YouTube did not accept the cookies. Sign in with the Firefox profile "
            f"{config.auth.firefox_profile}, or choose another with `youtube-tools config profile`."
        )
    CONSOLE.print("[green]Signed in:[/green] YouTube accepted the profile's cookies.")


if __name__ == "__main__":
    APP()
