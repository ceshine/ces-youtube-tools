"""Command-line entry point for ces-youtube-tools."""

import re
import time
import shutil
import inspect
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Literal, TypeVar, Annotated
from collections.abc import Callable

import typer
from rich.table import Table
from rich.markup import escape

from .common import fail, STDERR, CONSOLE, print_error, ConfigPathOption, load_config_or_exit
from .pickers import pick_one, pick_many, require_terminal, NotATerminalError
from .list_cmd import list_videos
from .site_cmd import build_site
from .guide_cmd import make_guide
from .config_cmd import (
    config_init,
    config_show,
    config_check,
    config_options,
    config_profile,
    config_template,
    config_add_channel,
    config_remove_channel,
)
from .backfill_cmd import backfill_guides
from .guide_report import report_guide, print_scan_warnings, make_guide_tools_with_notice
from .transcript_cmd import show_transcript
from ..guide import GuideError
from ..ytdlp import YtDlpError
from ..config import ChannelConfig
from ..config import DEFAULT_CONFIG_PATH
from ..youtube import VideoPager, VideoSummary
from ..pipeline import (
    GuideTools,
    make_ytdlp,
    GUIDE_ERRORS,
    PreparedGuide,
    prepare_guide,
    generate_guide,
)
from ..formatting import video_choice_label
from ..existing_guides import guides_to_replace

APP = typer.Typer(no_args_is_help=True, help="Browse YouTube channels and generate watching guides.")
CONFIG_APP = typer.Typer(no_args_is_help=True, help="Create and edit the configuration file.")
APP.add_typer(CONFIG_APP, name="config")

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


_ = command(CONFIG_APP, "init")(config_init)
_ = command(CONFIG_APP, "template")(config_template)
_ = command(CONFIG_APP, "profile")(config_profile)
_ = command(CONFIG_APP, "add-channel")(config_add_channel)
_ = command(CONFIG_APP, "remove-channel")(config_remove_channel)
_ = command(CONFIG_APP, "show")(config_show)
_ = command(CONFIG_APP, "options")(config_options)
_ = command(CONFIG_APP, "check")(config_check)


_ = command(APP, "list")(list_videos)


_ = command(APP, "transcript")(show_transcript)


_ = command(APP, "guide")(make_guide)


_ = command(APP, "backfill")(backfill_guides)
_ = command(APP, "site")(build_site)


# The cells questionary's checkbox draws before each item's label: " » " (or three spaces) and "○ " or "● ". See
# "questionary's checkbox draws five cells before each label" in docs/implementation_notes.md.
_MENU_MARK_WIDTH = 5
_NEXT_PAGE_LABEL = "▸ Next page (older videos)"


@dataclass(frozen=True)
class _PendingGuide:
    """A selected video in `browse` whose guide is to be written.

    Attributes:
        video (VideoSummary): The video, as listed.
        prepared (PreparedGuide): Its metadata and target, from `prepare_guide`.
        force (bool): The user agreed to replace the files already in the guide's place.
    """

    video: VideoSummary
    prepared: PreparedGuide
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


def _video_range(pager: VideoPager, number: int) -> tuple[int, int]:
    """Find where a fetched page's videos sit in the whole listing.

    Args:
        pager (VideoPager): The listing.
        number (int): The page's number. The page and every page before it must be fetched.

    Returns:
        tuple[int, int]: The 1-based positions of the page's first and last videos.
    """
    first = sum(len(pager.page(earlier)) for earlier in range(1, number)) + 1
    return first, first + len(pager.page(number)) - 1


def _video_menu_title(pager: VideoPager, number: int, channel_name: str, elsewhere: int) -> str:
    """Build the title of a page's video menu.

    Args:
        pager (VideoPager): The listing.
        number (int): The page's number. The page must be fetched.
        channel_name (str): The channel's name.
        elsewhere (int): Number of videos selected on other pages.

    Returns:
        str: The title, e.g. `Videos 21–40 of NAME (3 selected on other pages) (Space selects, Enter confirms)`.
    """
    if number == 1:
        title = f"Latest videos of {channel_name}"
    else:
        first, last = _video_range(pager, number)
        title = f"Videos {first}–{last} of {channel_name}"
    if elsewhere:
        title += f" ({elsewhere} selected on other pages)"
    return f"{title} (Space selects, Enter confirms)"


def _fetch_next_page(pager: VideoPager, channel_name: str) -> bool:
    """Fetch the page after the last fetched one, and say why not when it has no videos.

    Args:
        pager (VideoPager): The listing.
        channel_name (str): The channel's name, for the status line.

    Returns:
        bool: True if the new page has videos. False if it is empty or yt-dlp failed; the pager is then unchanged
            apart from `has_more`, so the current page can be shown again.

    Raises:
        KeyboardInterrupt: If the user pressed Ctrl+C while the page was loading. The pager is unchanged.
    """
    try:
        with CONSOLE.status(f"Fetching older videos of {channel_name}..."):
            older = pager.page(pager.page_count + 1)
    except YtDlpError as exc:
        print_error(str(exc))
        return False
    if not older:
        CONSOLE.print("No older videos.")
        return False
    return True


def _pick_videos(pager: VideoPager, channel_name: str) -> list[VideoSummary] | None:
    """Let the user pick videos from a channel's listing, a page at a time.

    After the videos, a page's menu lists "Previous page" on pages after the first, and "Next page" when there may be
    older videos. Ticking one of them moves to that page. The selection is kept by video ID across pages: videos
    ticked on a page stay selected when leaving it, a page shows its earlier ticks again, and unticking a video there
    drops it. Enter with neither page entry ticked confirms.

    Args:
        pager (VideoPager): The listing. Its first page must already be fetched and hold videos.
        channel_name (str): The channel's name, for the menu titles.

    Returns:
        list[VideoSummary] | None: The selected videos, newest first across pages, or None if the user pressed
            Ctrl+C in a menu or while a page was loading.
    """
    width = shutil.get_terminal_size().columns - _MENU_MARK_WIDTH
    now = time.time()
    selected: dict[str, VideoSummary] = {}
    number = 1
    while True:
        videos = pager.page(number)
        labels = [video_choice_label(video, now, width) for video in videos]
        previous_entry: int | None = None
        next_entry: int | None = None
        if number > 1:
            first, last = _video_range(pager, number - 1)
            labels.append(f"◂ Previous page (videos {first}–{last})")
            previous_entry = len(labels)
        if number < pager.page_count or pager.has_more:
            labels.append(_NEXT_PAGE_LABEL)
            next_entry = len(labels)
        on_page = {video.id for video in videos}
        elsewhere = sum(1 for video_id in selected if video_id not in on_page)
        answer = pick_many(
            _video_menu_title(pager, number, channel_name, elsewhere),
            labels,
            checked={item for item, video in enumerate(videos, start=1) if video.id in selected},
            require_selection=elsewhere == 0,
        )
        if answer is None:
            return None
        # Update the selection first, so videos ticked together with a page entry stay selected.
        for item, video in enumerate(videos, start=1):
            if item in answer:
                selected[video.id] = video
            else:
                selected.pop(video.id, None)
        go_previous = previous_entry in answer
        go_next = next_entry in answer
        if go_previous and go_next:
            CONSOLE.print(
                "Tick one page entry to change pages, or neither to confirm. Note that `a` and `i` tick them too.",
                style="yellow",
            )
        elif go_previous:
            number -= 1
        elif go_next:
            try:
                if number < pager.page_count or _fetch_next_page(pager, channel_name):
                    number += 1
            except KeyboardInterrupt:  # Ctrl+C while a page loads quits, as it does in a menu
                return None
        else:
            return [video for video in pager.fetched() if video.id in selected]


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
    print_error(f"{video.title} ({video.id}): {message}")
    return _Outcome("failed", message)


def _plan_guides(
    tools: GuideTools,
    videos: list[VideoSummary],
    target_dir: Path,
    *,
    refresh: bool,
) -> tuple[list[_PendingGuide], dict[str, _Outcome]]:
    """Load the selected videos' metadata and ask every overwrite question, before any guide is written.

    A video that fails, would touch a file an earlier one touches, or whose existing guide the user keeps, gets an
    outcome and is left out.

    Args:
        tools (GuideTools): The tools from `make_guide_tools_with_notice`.
        videos (list[VideoSummary]): The selected videos, in menu order.
        target_dir (Path): Directory for the guide files.
        refresh (bool): Ignore cached metadata.

    Returns:
        tuple[list[_PendingGuide], dict[str, _Outcome]]: The guides to write, in menu order, and the outcomes by video
            ID of the videos left out.
    """
    outcomes: dict[str, _Outcome] = {}
    prepared: list[tuple[VideoSummary, PreparedGuide]] = []
    for video in videos:
        try:
            guide = prepare_guide(tools, video.id, target_dir, refresh=refresh, status=STDERR.status)
        except GUIDE_ERRORS as exc:
            outcomes[video.id] = _plan_failure(video, str(exc))
            continue
        print_scan_warnings(guide)
        prepared.append((video, guide))

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


def _write_guides(tools: GuideTools, pending: list[_PendingGuide], *, refresh: bool) -> dict[str, _Outcome]:
    """Write the planned guides one at a time, carrying on after a failure.

    One at a time, not in parallel, to keep the automated traffic tied to the account low.

    Args:
        tools (GuideTools): The tools from `make_guide_tools_with_notice`.
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
            result = generate_guide(tools, guide.prepared, force=guide.force, refresh=refresh, status=STDERR.status)
        except GUIDE_ERRORS as exc:
            print_error(str(exc))
            outcomes[video.id] = _Outcome("failed", str(exc))
            continue
        report_guide(result)
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
    limit: Annotated[int, typer.Option("--limit", "-n", min=1, help="Number of videos per page.")] = 20,
    output_dir: Annotated[
        Path | None, typer.Option("--output-dir", "-o", help="Where to write the guides. Overrides the config.")
    ] = None,
    refresh: Annotated[bool, typer.Option("--refresh", help="Ignore cached metadata and transcripts.")] = False,
    config_path: ConfigPathOption = DEFAULT_CONFIG_PATH,
) -> None:
    """Pick a channel and one or more of its videos, then generate the videos' watching guides.

    The menus use the arrow keys, and Space selects videos. Older videos are a page away: tick "Next page" or
    "Previous page" and press Enter, and the videos selected so far stay selected. Every question about replacing an
    existing guide is asked before the first guide is written; the guides are then written one at a time. A video
    that fails does not stop the others.

    Args:
        channel (str | None): Channel name (case-insensitive) or ID from the configuration. None shows a menu.
        limit (int): Number of videos per page.
        output_dir (Path | None): Directory for the guide files.
        refresh (bool): Ignore cached metadata and transcripts.
        config_path (Path): Path of the configuration file.

    Returns:
        None

    Raises:
        typer.Exit: If no channel is configured, the channel is unknown, there is no terminal for the menus, or a
            step fails for any selected video. Status 0 when the user quits with Ctrl+C, in a menu or while videos load.
    """
    config = load_config_or_exit(config_path)
    if not config.channels:
        raise fail("No channels configured. Add one with `ces-youtube-tools config add-channel @handle`.")
    found: ChannelConfig | None = None
    if channel is not None:
        found = config.find_channel(channel)
        if found is None:
            names = ", ".join(c.name for c in config.channels)
            raise fail(f"No channel named {channel!r} in the configuration. Configured channels: {names}.")
    try:
        require_terminal()
    except NotATerminalError as exc:
        raise fail(f"{exc} For scripts, use `ces-youtube-tools guide VIDEO`.") from exc
    if found is None:
        if len(config.channels) == 1:
            found = config.channels[0]
        else:
            choice = pick_one("Channel", [_channel_label(c) for c in config.channels])
            if choice is None:
                CONSOLE.print("Bye.")
                return
            found = config.channels[choice - 1]

    pager = VideoPager(found, make_ytdlp(config, with_cookies=False), limit)
    try:
        with CONSOLE.status(f"Fetching the latest videos of {found.name}..."):
            first_page = pager.page(1)
    except YtDlpError as exc:
        raise fail(str(exc)) from exc
    except KeyboardInterrupt:
        CONSOLE.print("Bye.")
        return
    if not first_page:
        CONSOLE.print(f"No videos found for {found.name}.")
        return
    selected = _pick_videos(pager, found.name)
    if selected is None:
        CONSOLE.print("Bye.")
        return

    try:
        tools = make_guide_tools_with_notice(config)
    except GUIDE_ERRORS as exc:
        raise fail(str(exc)) from exc
    target_dir = output_dir or config.guide.output_dir or Path.cwd()
    pending, planned = _plan_guides(tools, selected, target_dir, refresh=refresh)
    outcomes = {**planned, **_write_guides(tools, pending, refresh=refresh)}
    if len(selected) > 1:
        CONSOLE.print(_outcomes_table(selected, outcomes))
    failed = sum(1 for outcome in outcomes.values() if outcome.status == "failed")
    if failed:
        if len(selected) > 1:
            raise fail(f"{failed} of {len(selected)} guides failed.")
        raise typer.Exit(code=1)
