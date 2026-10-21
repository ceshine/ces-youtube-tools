"""Command-line entry point for ces-youtube-tools."""

import re
import time
import shutil
import inspect
import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from dataclasses import dataclass
from collections.abc import Callable
from typing import Any, Literal, TypeVar, Annotated

import typer
from rich.table import Table
from rich.markup import escape
from rich.console import Console

from .menu import QuitMenu, parse_choice
from .guide import GuideError, GuideGenerator, guide_filename, default_template
from .video import VideoInfo, parse_video_id
from .ytdlp import YtDlp, YtDlpError
from .config import ChannelConfig
from .config import (
    AppConfig,
    ConfigError,
    dump_config,
    load_config,
    save_config,
    describe_options,
    DEFAULT_CONFIG_PATH,
)
from .firefox import (
    is_signed_in,
    FirefoxProfile,
    discover_all_profiles,
    cookies_from_browser_spec,
)
from .pickers import pick_one, pick_many, require_terminal, NotATerminalError
from .youtube import VideoSummary, verify_login, resolve_channel, list_latest_videos
from .backfill import rename_guide, BackfillError, plan_backfill, write_content, describe_changes
from .pipeline import CacheError, work_dir_for, load_transcript, load_video_info, make_transcript_provider
from .pipeline import write_guide, guides_to_replace, load_prompt_template, make_guide_generator
from .pipeline import GuideScan, GuideResult, check_existing_guides, find_guides_for_video
from .claude_cli import GENERATOR_NAME, ClaudeCliOptions
from .formatting import format_age, format_duration, video_choice_label
from .guide_html import expand_paths, GuideHtmlError
from .transcript import TranscriptError, TranscriptProvider
from .static_site import SiteError, write_site, collect_pages

APP = typer.Typer(no_args_is_help=True, help="Browse YouTube channels and generate watching guides.")
CONFIG_APP = typer.Typer(no_args_is_help=True, help="Create and edit the configuration file.")
APP.add_typer(CONFIG_APP, name="config")

CONSOLE = Console()
# Status and headers for commands whose stdout is data meant for piping.
STDERR = Console(stderr=True)

ConfigPathOption = Annotated[Path, typer.Option("--config", help="Path of the configuration file.")]
_DOCSTRING_SECTION = re.compile(r"^(Args|Returns|Raises|Yields|Examples?|Notes?):\s*$")

CommandFunction = TypeVar("CommandFunction", bound=Callable[..., Any])


def help_text(function: Callable[..., Any]) -> str:
    """Get a command's help text: its docstring up to the first Google-style section such as `Args:`.

    Typer shows the whole docstring otherwise, including the argument documentation the code style requires.

    Args:
        function (Callable[..., Any]): The command function.

    Returns:
        str: The docstring's description paragraphs.
    """
    lines: list[str] = []
    for line in inspect.cleandoc(function.__doc__ or "").splitlines():
        if _DOCSTRING_SECTION.match(line.strip()):
            break
        lines.append(line)
    return "\n".join(lines).strip()


def command(app: typer.Typer, name: str) -> Callable[[CommandFunction], CommandFunction]:
    """Register a function as a Typer command, with help text taken from its docstring description.

    Args:
        app (typer.Typer): The app or command group.
        name (str): The command name.

    Returns:
        Callable[[CommandFunction], CommandFunction]: The decorator.
    """

    def register(function: CommandFunction) -> CommandFunction:
        _ = app.command(name, help=help_text(function))(function)
        return function

    return register


@APP.callback()
def main() -> None:
    """Browse YouTube channels and generate watching guides.

    The callback keeps the app a command group, so subcommands are invoked by name.

    Returns:
        None
    """


def _print_error(message: str) -> None:
    """Print an error message.

    Args:
        message (str): The error message, as plain text. Square brackets in it are printed as they are, not read as
            Rich markup, because messages often hold paths, video titles and yt-dlp output such as `[youtube]`.

    Returns:
        None
    """
    STDERR.print(f"[red]Error:[/red] {escape(message)}", markup=True, highlight=False)


def _fail(message: str) -> typer.Exit:
    """Print an error message and build the exit to raise.

    Args:
        message (str): The error message, as plain text, as for `_print_error`.

    Returns:
        typer.Exit: An exit with status 1, for the caller to raise.
    """
    _print_error(message)
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
    choice = _choose("Profile to use for YouTube cookies", len(profiles), default=default)
    profile = profiles[choice - 1]
    if not signed_in[choice - 1]:
        CONSOLE.print(
            f"[yellow]Warning:[/yellow] {profile.name} is not signed in to YouTube. "
            "Sign in with that profile, then run `ces-youtube-tools config check`."
        )
    return profile


@command(CONFIG_APP, "init")
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


@command(CONFIG_APP, "template")
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
    path = _load(config_path).guide.prompt_template
    if not _write_default_template(path, force):
        raise _fail(f"{path} already exists. Use --force to replace it with the built-in template.")
    CONSOLE.print(f"Wrote the guide prompt template to {path}.")


@command(CONFIG_APP, "profile")
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


@command(CONFIG_APP, "add-channel")
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


@command(CONFIG_APP, "remove-channel")
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


@command(CONFIG_APP, "show")
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
        CONSOLE.print("No Firefox profile configured. Run `ces-youtube-tools config profile`.")
    elif not profile.is_dir():
        CONSOLE.print(f"[red]Firefox profile folder not found:[/red] {profile}")
    else:
        state = "has" if is_signed_in(profile) else "does not have"
        CONSOLE.print(f"The Firefox profile {state} YouTube sign-in cookies.")


@command(CONFIG_APP, "options")
def config_options() -> None:
    """List every configuration option with its type, default value and meaning.

    Returns:
        None
    """
    rows = describe_options(AppConfig)
    rows += describe_options(ClaudeCliOptions, f"guide.options.{GENERATOR_NAME}.")
    table = Table(title="Configuration options")
    table.add_column("Option", no_wrap=True)
    table.add_column("Type")
    table.add_column("Default", overflow="fold")
    table.add_column("Description")
    for row in rows:
        table.add_row(escape(row.key), escape(row.type_name), escape(row.default), escape(row.description))
    CONSOLE.print(table)


@command(CONFIG_APP, "check")
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
        raise _fail("No Firefox profile configured. Run `ces-youtube-tools config profile`.")
    if not verify_login(_make_ytdlp(config, with_cookies=True)):
        raise _fail(
            "YouTube did not accept the cookies. Sign in with the Firefox profile "
            f"{config.auth.firefox_profile}, or choose another with `ces-youtube-tools config profile`."
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


@command(APP, "list")
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
        names = (
            ", ".join(c.name for c in config.channels) or "none, add one with `ces-youtube-tools config add-channel`"
        )
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


@command(APP, "transcript")
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
    except (ValueError, YtDlpError, TranscriptError, CacheError) as exc:
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


@command(APP, "guide")
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
    config = _load(config_path)
    try:
        video_id = parse_video_id(video)
    except ValueError as exc:
        raise _fail(str(exc)) from exc
    _run_guide(config, video_id, output_dir or config.guide.output_dir or Path.cwd(), force=force, refresh=refresh)


@command(APP, "backfill")
def backfill_guides(
    paths: Annotated[list[str], typer.Argument(help="Markdown guides, or folders that hold them.")],
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Load the metadata and report, but change no guide.")
    ] = False,
    refresh: Annotated[bool, typer.Option("--refresh", help="Ignore cached metadata.")] = False,
    config_path: ConfigPathOption = DEFAULT_CONFIG_PATH,
) -> None:
    """Rewrite the front matter of guides from their videos' metadata, and rename them as `guide` would name them.

    Each video is found from the guide's front matter `video_id`, else from its links. Its metadata comes from the
    cache (unless `--refresh`), or is fetched with yt-dlp (with cookies, for members-only videos) and cached. A guide
    with a problem of its own (such as a symbolic link, or a video that cannot be loaded) is left as it is and listed,
    and the others are written. Two guides for one video, or a new name that is already taken, stop the run before
    any guide is changed. A folder stands for the `.md` files directly inside it.

    Args:
        paths (list[str]): Markdown guides, or folders that hold them.
        dry_run (bool): Load the metadata (filling the cache) and report, but change no guide.
        refresh (bool): Ignore cached metadata and fetch it again.
        config_path (Path): Path of the configuration file.

    Returns:
        None

    Raises:
        typer.Exit: If a path does not exist, two guides are about one video, a new name is taken, or any guide has a
            problem (left as it is, or written but not renamed).
    """
    config = _load(config_path)
    try:
        files = expand_paths(paths, Path.cwd())
    except GuideHtmlError as exc:
        raise _fail(str(exc)) from exc
    ytdlp = _make_ytdlp(config, with_cookies=True)  # members-only videos need cookies

    def load_video(video_id: str) -> VideoInfo:
        return load_video_info(video_id, ytdlp, work_dir_for(config.cache_dir, video_id), refresh=refresh)

    try:
        with STDERR.status("Loading video metadata..."):
            plan = plan_backfill(files, load_video, config.guide.file_name_zone())
    except BackfillError as exc:
        raise _fail(str(exc)) from exc
    problems = list(plan.problems)
    written = renamed = 0
    for update in plan.updates:
        if not dry_run:
            try:
                write_content(update)
            except BackfillError as exc:
                problems.append(str(exc))
                continue
        changes = describe_changes(update)
        if changes is not None:
            verb = "Would write" if dry_run else "Wrote"
            CONSOLE.print(f"{verb} {update.path}: {changes}", markup=False, highlight=False, soft_wrap=True)
            written += 1
        if update.new_path != update.path:
            if not dry_run:
                try:
                    rename_guide(update)
                except BackfillError as exc:
                    problems.append(str(exc))
                    continue
            verb = "Would rename" if dry_run else "Renamed"
            CONSOLE.print(
                f"{verb} {update.path} to {update.new_path.name}", markup=False, highlight=False, soft_wrap=True
            )
            renamed += 1
    if dry_run:
        counts = f"{written} to write, {renamed} to rename"
    else:
        counts = f"{written} written, {renamed} renamed"
    CONSOLE.print(f"{counts}, {len(plan.unchanged)} unchanged, {len(problems)} with problems.")
    if problems:
        details = "\n".join(f"- {problem}" for problem in problems)
        raise _fail(f"{len(problems)} of {len(files)} guides have problems:\n{details}")


@command(APP, "site")
def build_site(
    out_dir: Annotated[
        Path | None,
        typer.Option("--out", "-o", help="Folder to write the site into. Default: the configured site.output_dir."),
    ] = None,
    directory: Annotated[
        Path | None, typer.Argument(help="Folder of guides. Default: the configured guide.output_dir.")
    ] = None,
    include_members_only: Annotated[
        bool,
        typer.Option(
            "--include-members-only",
            help="Also publish members-only guides and guides whose availability is unknown. Only for a site "
            + "behind Cloudflare Access.",
        ),
    ] = False,
    config_path: ConfigPathOption = DEFAULT_CONFIG_PATH,
) -> None:
    """Build the guides into a static site for Cloudflare Pages.

    Writes one page per guide, an index, `robots.txt` and `_headers` into the output folder, which is cleared first
    if an earlier build wrote it. Members-only guides, and guides whose availability is unknown, are left out unless
    `--include-members-only` is given. Deploy with `npx wrangler pages deploy OUT`, to a project behind Cloudflare
    Access.

    Args:
        out_dir (Path | None): Folder to write the site into.
        directory (Path | None): Folder of guides.
        include_members_only (bool): Also publish members-only guides and guides whose availability is unknown.
        config_path (Path): Path of the configuration file.

    Returns:
        None

    Raises:
        typer.Exit: If there is no folder of guides or output folder, a guide cannot be built, or the output folder
            cannot be used.
    """
    if directory is None or out_dir is None:
        config = _load(config_path)
        directory = directory if directory is not None else config.guide.output_dir
        out_dir = out_dir if out_dir is not None else config.site.output_dir
    if directory is None:
        raise _fail("No folder given and guide.output_dir is not set. Pass the folder of guides.")
    if out_dir is None:
        raise _fail("No --out given and site.output_dir is not set. Pass the output folder with -o.")
    directory = directory.expanduser()
    out_dir = out_dir.expanduser()
    try:
        files = expand_paths([str(directory)], Path.cwd())
        build = collect_pages(files, include_members_only)
        write_site(build, out_dir)
    except (GuideHtmlError, SiteError) as exc:
        raise _fail(str(exc)) from exc
    pages = "1 guide page" if len(build.pages) == 1 else f"{len(build.pages)} guide pages"
    CONSOLE.print(f"Wrote {pages} and the index to {out_dir}.", markup=False, highlight=False, soft_wrap=True)
    if build.left_out:
        CONSOLE.print(
            f"Left out {len(build.left_out)} members-only guides or guides whose availability is unknown "
            + "(--include-members-only adds them).",
            markup=False,
            highlight=False,
            soft_wrap=True,
        )
    if include_members_only:
        STDERR.print(
            "[yellow]Warning:[/yellow] members-only guides are included. Deploy this build only to a project "
            + "behind Cloudflare Access.",
            highlight=False,
        )
    CONSOLE.print(f"Deploy with: npx wrangler pages deploy {out_dir}", markup=False, highlight=False, soft_wrap=True)


# The errors of a guide run that are reported as a message, without a traceback.
_GUIDE_ERRORS = (ValueError, YtDlpError, TranscriptError, GuideError, CacheError)


@dataclass(frozen=True)
class _GuideTools:
    """What writing guides needs, made once per command run.

    Attributes:
        ytdlp (YtDlp): The yt-dlp runner, with cookies because members-only videos need them.
        provider (TranscriptProvider): The configured transcript provider.
        generator (GuideGenerator): The configured guide generator.
        template (str): The prompt template.
        timezone (ZoneInfo | None): The time zone for the date in guide file names, or None for the system's.
    """

    ytdlp: YtDlp
    provider: TranscriptProvider
    generator: GuideGenerator
    template: str
    timezone: ZoneInfo | None


@dataclass(frozen=True)
class _PreparedGuide:
    """A video whose metadata is loaded, with where its guide goes and the files already about it.

    Attributes:
        info (VideoInfo): The video's metadata.
        work_dir (Path): The video's cache directory.
        scan (GuideScan): The files already about the video in the guide's folder.
        target (Path): Where the guide goes: always `target_dir / guide_filename(info, timezone)`, a bare file name
            in the guide's folder, so `target.parent` is that folder.
    """

    info: VideoInfo
    work_dir: Path
    scan: GuideScan
    target: Path


def _make_guide_tools(config: AppConfig) -> _GuideTools:
    """Make the yt-dlp runner, transcript provider, generator and prompt template for writing guides.

    Prints a notice when the built-in prompt template is used because the configured file does not exist.

    Args:
        config (AppConfig): The configuration.

    Returns:
        _GuideTools: The tools.

    Raises:
        ValueError: If the configured transcript provider or guide generator is unknown, or the generator's settings
            are invalid.
    """
    ytdlp = _make_ytdlp(config, with_cookies=True)  # members-only videos need cookies
    provider = make_transcript_provider(config, ytdlp)
    generator = make_guide_generator(config)
    template, is_default = load_prompt_template(config.guide.prompt_template)
    if is_default:
        STDERR.print(
            f"Using the built-in prompt template ({config.guide.prompt_template} does not exist). "
            "Run `ces-youtube-tools config template` for an editable copy.",
            markup=False,
            highlight=False,
        )
    return _GuideTools(ytdlp, provider, generator, template, config.guide.file_name_zone())


def _prepare_guide(
    config: AppConfig, tools: _GuideTools, video_id: str, target_dir: Path, *, refresh: bool
) -> _PreparedGuide:
    """Load a video's metadata, work out where its guide goes, and find the files already about the video.

    Prints a warning for each file in `target_dir` that could not be read or whose front matter is invalid.

    Args:
        config (AppConfig): The configuration.
        tools (_GuideTools): The tools from `_make_guide_tools`.
        video_id (str): YouTube video ID.
        target_dir (Path): Directory for the guide file.
        refresh (bool): Ignore cached metadata.

    Returns:
        _PreparedGuide: The video and its guide's path.

    Raises:
        CacheError: If the video's cache directory cannot be created, or its metadata cannot be saved there.
        YtDlpError: If yt-dlp fails, e.g. for a members-only video without a membership.
    """
    work_dir = work_dir_for(config.cache_dir, video_id)
    with STDERR.status("Fetching video metadata..."):
        info = load_video_info(video_id, tools.ytdlp, work_dir, refresh=refresh)
    scan = find_guides_for_video(target_dir, info.id)
    for warning in scan.warnings:
        STDERR.print(f"[yellow]Warning:[/yellow] {escape(warning)}", highlight=False, soft_wrap=True)
    return _PreparedGuide(info, work_dir, scan, target_dir / guide_filename(info, tools.timezone))


def _generate_guide(
    config: AppConfig, tools: _GuideTools, prepared: _PreparedGuide, *, force: bool, refresh: bool
) -> GuideResult:
    """Fetch a prepared video's transcript, generate its guide and write it.

    Args:
        config (AppConfig): The configuration.
        tools (_GuideTools): The tools from `_make_guide_tools`.
        prepared (_PreparedGuide): The video, from `_prepare_guide`.
        force (bool): Replace an existing guide for the video, and overwrite a file with the guide's name.
        refresh (bool): Ignore the cached transcript.

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
    with STDERR.status("Fetching transcript..."):
        transcript = load_transcript(prepared.info, tools.provider, prepared.work_dir, refresh=refresh)
    with STDERR.status(f"Writing the guide with {config.guide.generator} (this can take a few minutes)..."):
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


def _report_guide(result: GuideResult) -> None:
    """Print where a guide was written, the older guides it replaced, and what the link check found.

    Args:
        result (GuideResult): The written guide.

    Returns:
        None
    """
    links = result.links
    CONSOLE.print(f"Wrote {result.path}", markup=False, highlight=False)
    for old in result.removed:
        CONSOLE.print(f"Removed the older guide {old}", markup=False, highlight=False)
    CONSOLE.print(f"{links.links} timestamp links, {links.relabeled} labels corrected.")
    if links.out_of_range:
        times = ", ".join(format_duration(seconds) for seconds in links.out_of_range)
        STDERR.print(f"[yellow]Warning:[/yellow] links past the end of the video: {times}. Check those parts.")
    if links.links == 0:
        STDERR.print("[yellow]Warning:[/yellow] the guide has no timestamp links. Check the prompt template.")
    if result.raw_reply_error is not None:
        STDERR.print(
            f"[yellow]Warning:[/yellow] the raw reply was not kept in the cache: {escape(result.raw_reply_error)}",
            highlight=False,
            soft_wrap=True,
        )


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
        tools = _make_guide_tools(config)
        prepared = _prepare_guide(config, tools, video_id, target_dir, refresh=refresh)
        result = _generate_guide(config, tools, prepared, force=force, refresh=refresh)
    except _GUIDE_ERRORS as exc:
        raise _fail(str(exc)) from exc
    _report_guide(result)


# The cells questionary's checkbox draws before each item's label: " » " (or three spaces) and "○ " or "● ". See
# "questionary's checkbox draws five cells before each label" in docs/implementation_notes.md.
_MENU_MARK_WIDTH = 5


@dataclass(frozen=True)
class _PendingGuide:
    """A selected video in `browse` whose guide is to be written.

    Attributes:
        video (VideoSummary): The video, as listed.
        prepared (_PreparedGuide): Its metadata and target, from `_prepare_guide`.
        force (bool): The user agreed to replace the files already in the guide's place.
    """

    video: VideoSummary
    prepared: _PreparedGuide
    force: bool


@dataclass(frozen=True)
class _Outcome:
    """What happened to one selected video in `browse`.

    Attributes:
        status (Literal["written", "kept", "failed"]): The guide was written, the existing guide was kept, or a step
            failed.
        detail (str): The guide's path, the kept files, or why it failed.
    """

    status: Literal["written", "kept", "failed"]
    detail: str


def _channel_label(channel: ChannelConfig) -> str:
    """Build a channel's menu label.

    Args:
        channel (ChannelConfig): The channel.

    Returns:
        str: The name, marked when members-only videos are listed.
    """
    return f"{channel.name} (members-only listed)" if channel.members else channel.name


def _confirm_replace(replaced: list[Path]) -> bool:
    """Ask whether a new guide may replace the files already in its place.

    Args:
        replaced (list[Path]): The files, from `guides_to_replace`.

    Returns:
        bool: True if the user agreed.
    """
    names = ", ".join(str(path) for path in replaced)
    question = f"{names} already exists. Replace it?" if len(replaced) == 1 else f"{names} already exist. Replace them?"
    return typer.confirm(question, default=False)


def _plan_failure(video: VideoSummary, message: str) -> _Outcome:
    """Print an error found while planning, naming the video, since no `[n/total]` header has said which it is.

    Args:
        video (VideoSummary): The video.
        message (str): What went wrong.

    Returns:
        _Outcome: The video's failed outcome, with the message alone, as the summary row names the video.
    """
    _print_error(f"{video.title} ({video.id}): {message}")
    return _Outcome("failed", message)


def _plan_guides(
    config: AppConfig,
    tools: _GuideTools,
    videos: list[VideoSummary],
    target_dir: Path,
    *,
    refresh: bool,
) -> tuple[list[_PendingGuide], dict[str, _Outcome]]:
    """Load the selected videos' metadata and ask every overwrite question, before any guide is written.

    A video that fails, would touch a file an earlier one touches, or whose existing guide the user keeps, gets an
    outcome and is left out.

    Args:
        config (AppConfig): The configuration.
        tools (_GuideTools): The tools from `_make_guide_tools`.
        videos (list[VideoSummary]): The selected videos, in menu order.
        target_dir (Path): Directory for the guide files.
        refresh (bool): Ignore cached metadata.

    Returns:
        tuple[list[_PendingGuide], dict[str, _Outcome]]: The guides to write, in menu order, and the outcomes by video
            ID of the videos left out.
    """
    outcomes: dict[str, _Outcome] = {}
    prepared: list[tuple[VideoSummary, _PreparedGuide]] = []
    for video in videos:
        try:
            prepared.append((video, _prepare_guide(config, tools, video.id, target_dir, refresh=refresh)))
        except _GUIDE_ERRORS as exc:
            outcomes[video.id] = _plan_failure(video, str(exc))

    # Each video's scan is taken now, before any guide is written, so a video's write must not touch a file an
    # earlier video's write touches. Two videos with the same upload day and cleaned title get the same file name,
    # and one video's file name can be an older guide of another, which that video's write would then remove.
    claimed: dict[Path, VideoSummary] = {}
    pending: list[_PendingGuide] = []
    for video, guide in prepared:
        try:
            replaced = guides_to_replace(guide.target, guide.scan)
        except GuideError as exc:
            outcomes[video.id] = _plan_failure(video, str(exc))
            continue
        touched = [guide.target, *replaced]
        clash = next((path for path in touched if path in claimed), None)
        if clash is not None:
            first = claimed[clash]
            message = (
                f"{clash} is also the guide file, or an older guide, of {first.title} ({first.id}). Write this one "
                f"to another folder with `ces-youtube-tools guide {video.id} -o DIR`."
            )
            outcomes[video.id] = _plan_failure(video, message)
            continue
        claimed.update(dict.fromkeys(touched, video))
        if replaced and not _confirm_replace(replaced):
            CONSOLE.print("Kept the existing guide.")
            outcomes[video.id] = _Outcome("kept", ", ".join(str(path) for path in replaced))
            continue
        pending.append(_PendingGuide(video, guide, force=bool(replaced)))
    return pending, outcomes


def _write_guides(
    config: AppConfig, tools: _GuideTools, pending: list[_PendingGuide], *, refresh: bool
) -> dict[str, _Outcome]:
    """Write the planned guides one at a time, carrying on after a failure.

    One at a time, not in parallel, to keep the automated traffic tied to the account low.

    Args:
        config (AppConfig): The configuration.
        tools (_GuideTools): The tools from `_make_guide_tools`.
        pending (list[_PendingGuide]): The guides to write, from `_plan_guides`.
        refresh (bool): Ignore the cached transcript.

    Returns:
        dict[str, _Outcome]: The outcomes by video ID of the guides in `pending`.
    """
    outcomes: dict[str, _Outcome] = {}
    for number, guide in enumerate(pending, start=1):
        video = guide.video
        CONSOLE.print(f"[{number}/{len(pending)}] {video.title} ({video.url})", markup=False, highlight=False)
        try:
            result = _generate_guide(config, tools, guide.prepared, force=guide.force, refresh=refresh)
        except _GUIDE_ERRORS as exc:
            _print_error(str(exc))
            outcomes[video.id] = _Outcome("failed", str(exc))
            continue
        _report_guide(result)
        outcomes[video.id] = _Outcome("written", str(result.path))
    return outcomes


def _outcomes_table(videos: list[VideoSummary], outcomes: dict[str, _Outcome]) -> Table:
    """Build the summary table of a `browse` run.

    Args:
        videos (list[VideoSummary]): The selected videos, in menu order.
        outcomes (dict[str, _Outcome]): Outcomes by video ID, one for each video.

    Returns:
        Table: The table.
    """
    styles = {"written": "green", "kept": "yellow", "failed": "red"}
    table = Table(title="Guides")
    table.add_column("Video")
    table.add_column("Result")
    table.add_column("Details")
    for video in videos:
        outcome = outcomes[video.id]
        table.add_row(escape(video.title), f"[{styles[outcome.status]}]{outcome.status}[/]", escape(outcome.detail))
    return table


@command(APP, "browse")
def browse(
    channel: Annotated[str | None, typer.Argument(help="Channel name or ID. Omit to pick from a menu.")] = None,
    limit: Annotated[int, typer.Option("--limit", "-n", min=1, help="Number of videos to show.")] = 20,
    output_dir: Annotated[
        Path | None, typer.Option("--output-dir", "-o", help="Where to write the guides. Overrides the config.")
    ] = None,
    refresh: Annotated[bool, typer.Option("--refresh", help="Ignore cached metadata and transcripts.")] = False,
    config_path: ConfigPathOption = DEFAULT_CONFIG_PATH,
) -> None:
    """Pick a channel and one or more of its latest videos, then generate the videos' watching guides.

    The menus use the arrow keys, and Space selects videos. Every question about replacing an existing guide is asked
    before the first guide is written; the guides are then written one at a time. A video that fails does not stop
    the others.

    Args:
        channel (str | None): Channel name (case-insensitive) or ID from the configuration. None shows a menu.
        limit (int): Number of videos to show.
        output_dir (Path | None): Directory for the guide files.
        refresh (bool): Ignore cached metadata and transcripts.
        config_path (Path): Path of the configuration file.

    Returns:
        None

    Raises:
        typer.Exit: If no channel is configured, the channel is unknown, there is no terminal for the menus, or a
            step fails for any selected video. Status 0 when the user quits a menu with Ctrl+C.
    """
    config = _load(config_path)
    if not config.channels:
        raise _fail("No channels configured. Add one with `ces-youtube-tools config add-channel @handle`.")
    found: ChannelConfig | None = None
    if channel is not None:
        found = config.find_channel(channel)
        if found is None:
            names = ", ".join(c.name for c in config.channels)
            raise _fail(f"No channel named {channel!r} in the configuration. Configured channels: {names}.")
    try:
        require_terminal()
    except NotATerminalError as exc:
        raise _fail(f"{exc} For scripts, use `ces-youtube-tools guide VIDEO`.") from exc
    if found is None:
        if len(config.channels) == 1:
            found = config.channels[0]
        else:
            choice = pick_one("Channel", [_channel_label(c) for c in config.channels])
            if choice is None:
                CONSOLE.print("Bye.")
                return
            found = config.channels[choice - 1]

    try:
        with CONSOLE.status(f"Fetching the latest videos of {found.name}..."):
            videos = list_latest_videos(found, _make_ytdlp(config, with_cookies=False), limit)
    except YtDlpError as exc:
        raise _fail(str(exc)) from exc
    if not videos:
        CONSOLE.print(f"No videos found for {found.name}.")
        return
    width = shutil.get_terminal_size().columns - _MENU_MARK_WIDTH
    now = time.time()
    labels = [video_choice_label(video, now, width) for video in videos]
    choices = pick_many(f"Latest videos of {found.name} (Space selects, Enter confirms)", labels)
    if choices is None:
        CONSOLE.print("Bye.")
        return
    selected = [videos[number - 1] for number in choices]

    try:
        tools = _make_guide_tools(config)
    except _GUIDE_ERRORS as exc:
        raise _fail(str(exc)) from exc
    target_dir = output_dir or config.guide.output_dir or Path.cwd()
    pending, planned = _plan_guides(config, tools, selected, target_dir, refresh=refresh)
    outcomes = {**planned, **_write_guides(config, tools, pending, refresh=refresh)}
    if len(selected) > 1:
        CONSOLE.print(_outcomes_table(selected, outcomes))
    failed = sum(1 for outcome in outcomes.values() if outcome.status == "failed")
    if failed:
        if len(selected) > 1:
            raise _fail(f"{failed} of {len(selected)} guides failed.")
        raise typer.Exit(code=1)


if __name__ == "__main__":
    APP()
