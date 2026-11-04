"""The `backfill` command: rewrite guides' front matter and names from their videos' metadata."""

from pathlib import Path
from typing import Annotated

import typer

from .common import fail, STDERR, CONSOLE, ConfigPathOption, load_config_or_exit
from ..cache import work_dir_for, load_video_info
from ..video import VideoInfo
from ..config import DEFAULT_CONFIG_PATH
from ..backfill import rename_guide, BackfillError, plan_backfill, write_content, describe_changes
from ..pipeline import make_ytdlp
from ..guide_html import expand_paths, GuideHtmlError


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
    config = load_config_or_exit(config_path)
    try:
        files = expand_paths(paths, Path.cwd())
    except GuideHtmlError as exc:
        raise fail(str(exc)) from exc
    ytdlp = make_ytdlp(config, with_cookies=True)  # members-only videos need cookies

    def load_video(video_id: str) -> VideoInfo:
        return load_video_info(video_id, ytdlp, work_dir_for(config.cache_dir, video_id), refresh=refresh)

    try:
        with STDERR.status("Loading video metadata..."):
            plan = plan_backfill(files, load_video, config.guide.file_name_zone())
    except BackfillError as exc:
        raise fail(str(exc)) from exc
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
        raise fail(f"{len(problems)} of {len(files)} guides have problems:\n{details}")
