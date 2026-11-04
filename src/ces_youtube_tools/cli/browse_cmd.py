"""The `browse` command: pick a channel and videos from menus, then write the videos' guides."""

from pathlib import Path
from dataclasses import dataclass
from typing import Literal, Annotated

import typer
from rich.table import Table
from rich.markup import escape

from .common import fail, STDERR, CONSOLE, print_error, ConfigPathOption, load_config_or_exit
from .pickers import pick_one, require_terminal, NotATerminalError
from .video_menu import pick_videos
from .guide_report import report_guide, print_scan_warnings, make_guide_tools_with_notice
from ..guide import GuideError
from ..ytdlp import YtDlpError
from ..config import ChannelConfig, DEFAULT_CONFIG_PATH
from ..youtube import VideoPager, VideoSummary
from ..pipeline import GuideTools, make_ytdlp, GUIDE_ERRORS, PreparedGuide, prepare_guide, generate_guide
from ..existing_guides import guides_to_replace


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
    selected = pick_videos(pager, found.name)
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
