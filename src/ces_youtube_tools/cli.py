"""Command-line entry point for ces-youtube-tools."""

import re
import time
import inspect
import datetime
from pathlib import Path
from collections.abc import Callable
from typing import Any, TypeVar, Annotated

import typer
from rich.table import Table
from rich.markup import escape
from rich.console import Console

from .menu import QuitMenu, parse_choice
from .guide import GuideError, guide_filename, default_template
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
from .youtube import VideoSummary, verify_login, resolve_channel, list_latest_videos
from .backfill import BackfillError, plan_backfill, write_front_matter
from .pipeline import CacheError, work_dir_for, load_transcript, load_video_info, make_transcript_provider
from .pipeline import write_guide, load_prompt_template, make_guide_generator
from .claude_cli import GENERATOR_NAME, ClaudeCliOptions
from .formatting import format_age, format_duration
from .guide_html import expand_paths, GuideHtmlError
from .transcript import TranscriptError
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


def _fail(message: str) -> typer.Exit:
    """Print an error message and build the exit to raise.

    Args:
        message (str): The error message, as plain text. Square brackets in it are printed as they are, not read as
            Rich markup, because messages often hold paths, video titles and yt-dlp output such as `[youtube]`.

    Returns:
        typer.Exit: An exit with status 1, for the caller to raise.
    """
    STDERR.print(f"[red]Error:[/red] {escape(message)}", markup=True, highlight=False)
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
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing guide file.")] = False,
    refresh: Annotated[bool, typer.Option("--refresh", help="Ignore cached metadata and transcript.")] = False,
    config_path: ConfigPathOption = DEFAULT_CONFIG_PATH,
) -> None:
    """Generate a watching guide for a video and write it as Markdown.

    The guide goes to `--output-dir`, else the configured `guide.output_dir`, else the current directory.

    Args:
        video (str): Video URL or ID.
        output_dir (Path | None): Directory for the guide file.
        force (bool): Overwrite an existing guide file.
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
    config_path: ConfigPathOption = DEFAULT_CONFIG_PATH,
) -> None:
    """Add front matter to guides written before guides had it.

    Each video is found from the guide's links. Its metadata comes from the cache, or is fetched with yt-dlp (with
    cookies, for members-only videos) and cached. Guides that already have front matter are skipped. If any guide's
    metadata cannot be loaded, no guide is changed. A folder stands for the `.md` files directly inside it.

    Args:
        paths (list[str]): Markdown guides, or folders that hold them.
        dry_run (bool): Load the metadata (filling the cache) and report, but change no guide.
        config_path (Path): Path of the configuration file.

    Returns:
        None

    Raises:
        typer.Exit: If a path does not exist, or a guide cannot be read, loaded or written.
    """
    config = _load(config_path)
    try:
        files = expand_paths(paths, Path.cwd())
    except GuideHtmlError as exc:
        raise _fail(str(exc)) from exc
    ytdlp = _make_ytdlp(config, with_cookies=True)  # members-only videos need cookies

    def load_video(video_id: str) -> VideoInfo:
        return load_video_info(video_id, ytdlp, work_dir_for(config.cache_dir, video_id))

    try:
        with STDERR.status("Loading video metadata..."):
            plan = plan_backfill(files, load_video)
    except BackfillError as exc:
        raise _fail(str(exc)) from exc
    written = 0
    for guide in plan.pending:
        if dry_run:
            CONSOLE.print(f"Would write {guide.path}", markup=False, highlight=False, soft_wrap=True)
            continue
        try:
            write_front_matter(guide)
        except BackfillError as exc:
            count = "1 guide was" if written == 1 else f"{written} guides were"
            raise _fail(f"{exc}. {count} written before it; run the command again to finish.") from exc
        CONSOLE.print(f"Wrote {guide.path}", markup=False, highlight=False, soft_wrap=True)
        written += 1
    action = "to write" if dry_run else "written"
    CONSOLE.print(f"{len(plan.pending)} {action}, {len(plan.skipped)} skipped (already have front matter).")


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


def _run_guide(
    config: AppConfig,
    video_id: str,
    target_dir: Path,
    *,
    force: bool,
    refresh: bool,
    ask_to_overwrite: bool = False,
) -> None:
    """Fetch a video's transcript, generate its guide, write it and report the result.

    Args:
        config (AppConfig): The configuration.
        video_id (str): YouTube video ID.
        target_dir (Path): Directory for the guide file.
        force (bool): Overwrite an existing guide file.
        refresh (bool): Ignore cached metadata and transcript.
        ask_to_overwrite (bool): If the guide file exists, ask whether to overwrite it instead of failing.

    Returns:
        None

    Raises:
        typer.Exit: If any step fails.
    """
    try:
        ytdlp = _make_ytdlp(config, with_cookies=True)  # members-only videos need cookies
        provider = make_transcript_provider(config, ytdlp)
        generator = make_guide_generator(config)
        work_dir = work_dir_for(config.cache_dir, video_id)
        template, is_default = load_prompt_template(config.guide.prompt_template)
        if is_default:
            STDERR.print(
                f"Using the built-in prompt template ({config.guide.prompt_template} does not exist). "
                "Run `ces-youtube-tools config template` for an editable copy.",
                markup=False,
                highlight=False,
            )
        with STDERR.status("Fetching video metadata..."):
            info = load_video_info(video_id, ytdlp, work_dir, refresh=refresh)
        existing = target_dir / guide_filename(info)
        if ask_to_overwrite and not force and existing.exists():
            if not typer.confirm(f"{existing} already exists. Overwrite it?", default=False):
                CONSOLE.print("Kept the existing guide.")
                return
            force = True
        with STDERR.status("Fetching transcript..."):
            transcript = load_transcript(info, provider, work_dir, refresh=refresh)
        with STDERR.status(f"Writing the guide with {config.guide.generator} (this can take a few minutes)..."):
            result = write_guide(
                info,
                transcript,
                template,
                generator,
                work_dir,
                target_dir,
                datetime.datetime.now().astimezone().date(),
                force=force,
            )
    except (ValueError, YtDlpError, TranscriptError, GuideError, CacheError) as exc:
        raise _fail(str(exc)) from exc

    links = result.links
    CONSOLE.print(f"Wrote {result.path}", markup=False, highlight=False)
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


def _channels_table(channels: list[ChannelConfig]) -> Table:
    """Build a table of configured channels for display.

    Args:
        channels (list[ChannelConfig]): The channels.

    Returns:
        Table: The table.
    """
    table = Table(title="Channels")
    table.add_column("#", justify="right")
    table.add_column("Name")
    table.add_column("Members-only")
    table.add_column("URL")
    for index, channel in enumerate(channels, start=1):
        table.add_row(str(index), channel.name, "listed" if channel.members else "", channel.url)
    return table


@command(APP, "browse")
def browse(
    channel: Annotated[str | None, typer.Argument(help="Channel name or ID. Omit to pick from a menu.")] = None,
    limit: Annotated[int, typer.Option("--limit", "-n", min=1, help="Number of videos to show.")] = 20,
    output_dir: Annotated[
        Path | None, typer.Option("--output-dir", "-o", help="Where to write the guide. Overrides the config.")
    ] = None,
    refresh: Annotated[bool, typer.Option("--refresh", help="Ignore cached metadata and transcript.")] = False,
    config_path: ConfigPathOption = DEFAULT_CONFIG_PATH,
) -> None:
    """Pick a channel and one of its latest videos, then generate the video's watching guide.

    Args:
        channel (str | None): Channel name (case-insensitive) or ID from the configuration. None shows a menu.
        limit (int): Number of videos to show.
        output_dir (Path | None): Directory for the guide file.
        refresh (bool): Ignore cached metadata and transcript.
        config_path (Path): Path of the configuration file.

    Returns:
        None

    Raises:
        typer.Exit: If no channel is configured, the channel is unknown, or a step fails. Status 0 when the user quits.
    """
    config = _load(config_path)
    if not config.channels:
        raise _fail("No channels configured. Add one with `ces-youtube-tools config add-channel @handle`.")
    if channel is not None:
        found = config.find_channel(channel)
        if found is None:
            names = ", ".join(c.name for c in config.channels)
            raise _fail(f"No channel named {channel!r} in the configuration. Configured channels: {names}.")
    elif len(config.channels) == 1:
        found = config.channels[0]
    else:
        CONSOLE.print(_channels_table(config.channels))
        found = config.channels[_choose("Channel", len(config.channels)) - 1]

    try:
        with CONSOLE.status(f"Fetching the latest videos of {found.name}..."):
            videos = list_latest_videos(found, _make_ytdlp(config, with_cookies=False), limit)
    except YtDlpError as exc:
        raise _fail(str(exc)) from exc
    if not videos:
        CONSOLE.print(f"No videos found for {found.name}.")
        return
    CONSOLE.print(_videos_table(f"Latest videos of {found.name}", videos, time.time()))
    video = videos[_choose("Video to write a guide for", len(videos)) - 1]
    CONSOLE.print(f"Selected: {video.title} ({video.url})", markup=False, highlight=False)
    _run_guide(
        config,
        video.id,
        output_dir or config.guide.output_dir or Path.cwd(),
        force=False,
        refresh=refresh,
        ask_to_overwrite=True,
    )


if __name__ == "__main__":
    APP()
