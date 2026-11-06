"""The `config` commands: create and edit the configuration file."""

from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table
from rich.markup import escape

from .menu import QuitMenu, parse_choice
from .common import fail, CONSOLE, ConfigPathOption, load_config_or_exit
from ..guide import default_template
from ..ytdlp import YtDlpError
from ..config import AppConfig, dump_config, save_config, describe_options, DEFAULT_CONFIG_PATH
from ..firefox import is_signed_in, FirefoxProfile, discover_all_profiles
from ..youtube import verify_login, resolve_channel
from ..pipeline import make_ytdlp
from ..generators import GUIDE_GENERATORS, make_guide_generator


def _choose(prompt: str, count: int, default: int | None = None) -> int:
    """Ask for a menu item number until the answer is valid.

    Args:
        prompt (str): The question.
        count (int): Number of items in the menu.
        default (int | None): Item chosen by an empty answer, or None if an answer is required.

    Returns:
        int: The chosen item number, from 1 to `count`.

    Raises:
        typer.Exit: With status 0 if the user quits with `q`.
    """
    while True:
        answer = typer.prompt(
            prompt, default=str(default) if default is not None else "", show_default=default is not None
        )
        try:
            return parse_choice(answer, count, default)
        except QuitMenu:
            CONSOLE.print("Bye.")
            raise typer.Exit() from None
        except ValueError as exc:
            CONSOLE.print(str(exc))


def _pick_profile() -> FirefoxProfile:
    """Show the Firefox profiles and ask the user to pick one.

    Returns:
        FirefoxProfile: The chosen profile.

    Raises:
        typer.Exit: If no Firefox profiles are found.
    """
    profiles = discover_all_profiles(Path.home())
    if not profiles:
        raise fail("No Firefox profiles found. Create one in Firefox (about:profiles) first.")

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
    choice = _choose("Profile to use for YouTube cookies", len(profiles), default=default)
    profile = profiles[choice - 1]
    if not signed_in[choice - 1]:
        CONSOLE.print(
            f"[yellow]Warning:[/yellow] {profile.name} is not signed in to YouTube. "
            "Sign in with that profile, then run `ces-youtube-tools config check`."
        )
    return profile


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
        raise fail(f"{config_path} already exists. Use --force to overwrite it, or `config profile` to change it.")
    profile = _pick_profile()
    config = AppConfig()
    config.auth.firefox_profile = profile.path
    config.guide.prompt_template = config_path.parent / "guide_prompt.md"
    save_config(config, config_path)
    CONSOLE.print(f"Saved {config_path}.")
    if _write_default_template(config.guide.prompt_template, force=False):
        CONSOLE.print(
            f"Wrote the guide prompt template to {config.guide.prompt_template}. Edit it to change the guides."
        )
    CONSOLE.print("Next, add channels with `ces-youtube-tools config add-channel @handle`.")


def _write_default_template(path: Path, force: bool) -> bool:
    """Write the built-in prompt template to a file.

    Args:
        path (Path): Where to write it.
        force (bool): Overwrite an existing file.

    Returns:
        bool: True if the file was written, False if it already existed.
    """
    if path.exists() and not force:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(default_template(), encoding="utf-8")
    return True


def config_template(
    force: Annotated[bool, typer.Option("--force", help="Overwrite an edited template.")] = False,
    config_path: ConfigPathOption = DEFAULT_CONFIG_PATH,
) -> None:
    """Write the built-in guide prompt template to the configured `prompt_template` path, for editing.

    Args:
        force (bool): Overwrite an existing template file.
        config_path (Path): Path of the configuration file.

    Returns:
        None

    Raises:
        typer.Exit: If the template file exists and `force` is not set.
    """
    path = load_config_or_exit(config_path).guide.prompt_template
    if not _write_default_template(path, force):
        raise fail(f"{path} already exists. Use --force to replace it with the built-in template.")
    CONSOLE.print(f"Wrote the guide prompt template to {path}.")


def config_profile(config_path: ConfigPathOption = DEFAULT_CONFIG_PATH) -> None:
    """Choose a different Firefox profile for YouTube cookies.

    Args:
        config_path (Path): Path of the configuration file.

    Returns:
        None
    """
    config = load_config_or_exit(config_path)
    profile = _pick_profile()
    config.auth.firefox_profile = profile.path
    save_config(config, config_path)
    CONSOLE.print(f"Using profile {profile.name} ({profile.path}).")


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
    config = load_config_or_exit(config_path)
    try:
        resolved = resolve_channel(channel, make_ytdlp(config, with_cookies=False))
    except (ValueError, YtDlpError) as exc:
        raise fail(str(exc)) from exc
    resolved.members = members

    existing = config.find_channel(resolved.channel_id)
    if existing is not None:
        raise fail(f"{existing.name} ({existing.channel_id}) is already in the configuration.")

    CONSOLE.print(f"Found [bold]{resolved.name}[/bold] ({resolved.channel_id}) at {resolved.url}", highlight=False)
    if not yes and not typer.confirm("Add this channel?", default=True):
        raise typer.Exit()
    config.channels.append(resolved)
    save_config(config, config_path)
    CONSOLE.print(f"Added {resolved.name}.")


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
    config = load_config_or_exit(config_path)
    found = config.find_channel(channel)
    if found is None:
        raise fail(f"No channel named {channel!r} in the configuration.")
    config.channels.remove(found)
    save_config(config, config_path)
    CONSOLE.print(f"Removed {found.name}.")


def _print_generator(config: AppConfig) -> None:
    """Print which generator writes the guides, or why its `[guide.options.<name>]` settings are invalid.

    Args:
        config (AppConfig): The configuration.

    Returns:
        None
    """
    try:
        generator = make_guide_generator(config)
    except ValueError as exc:
        CONSOLE.print(f"[red]Guide generator:[/red] {escape(str(exc))}", highlight=False)
        return
    text = f"Guides are written by {generator.harness} with {generator.model}"
    if generator.effort is not None:
        text += f" ({generator.effort} effort)"
    CONSOLE.print(f"{text}.", markup=False, highlight=False)


def config_show(config_path: ConfigPathOption = DEFAULT_CONFIG_PATH) -> None:
    """Print the configuration, the guide generator, and whether the Firefox profile has YouTube sign-in cookies.

    The generator is built from its `[guide.options.<name>]` settings, so invalid settings show up without running
    `guide` or `browse`.

    Args:
        config_path (Path): Path of the configuration file.

    Returns:
        None
    """
    config = load_config_or_exit(config_path)
    CONSOLE.print(f"[bold]{config_path}[/bold]")
    CONSOLE.print(dump_config(config), markup=False, highlight=False)
    _print_generator(config)
    profile = config.auth.firefox_profile
    if profile is None:
        CONSOLE.print("No Firefox profile configured. Run `ces-youtube-tools config profile`.")
    elif not profile.is_dir():
        CONSOLE.print(f"[red]Firefox profile folder not found:[/red] {profile}")
    else:
        state = "has" if is_signed_in(profile) else "does not have"
        CONSOLE.print(f"The Firefox profile {state} YouTube sign-in cookies.")


def config_options() -> None:
    """List every configuration option with its type, default value and meaning.

    Returns:
        None
    """
    rows = describe_options(AppConfig)
    for name, spec in GUIDE_GENERATORS.items():
        rows += describe_options(spec.options_model, f"guide.options.{name}.")
    table = Table(title="Configuration options")
    table.add_column("Option", no_wrap=True)
    table.add_column("Type")
    table.add_column("Default", overflow="fold")
    table.add_column("Description")
    for row in rows:
        table.add_row(escape(row.key), escape(row.type_name), escape(row.default), escape(row.description))
    CONSOLE.print(table)


def config_check(config_path: ConfigPathOption = DEFAULT_CONFIG_PATH) -> None:
    """Check with YouTube that the configured profile is signed in.

    Args:
        config_path (Path): Path of the configuration file.

    Returns:
        None

    Raises:
        typer.Exit: If no profile is configured or YouTube rejects the cookies.
    """
    config = load_config_or_exit(config_path)
    if config.auth.firefox_profile is None:
        raise fail("No Firefox profile configured. Run `ces-youtube-tools config profile`.")
    if not verify_login(make_ytdlp(config, with_cookies=True)):
        raise fail(
            "YouTube did not accept the cookies. Sign in with the Firefox profile "
            f"{config.auth.firefox_profile}, or choose another with `ces-youtube-tools config profile`."
        )
    CONSOLE.print("[green]Signed in:[/green] YouTube accepted the profile's cookies.")
