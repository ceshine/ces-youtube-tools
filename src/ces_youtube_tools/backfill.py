"""Rewrite the front matter of guides from their videos' metadata, and rename them as `guide` would name them now.

The work is done in two passes. The first reads every guide, loads its video's metadata and builds the new file. A
guide with a problem of its own (a symbolic link, unreadable, invalid front matter, no video ID, metadata that
cannot be loaded) is left as it is and reported, and the others go on. A problem between guides (two guides for one
video, a new name that is already taken) stops the run before any guide is changed. The second pass writes and
renames the guides, one at a time.
"""

import shutil
import datetime
import contextlib
from pathlib import Path
from dataclasses import dataclass
from collections.abc import Callable

from .guide import guide_filename
from .video import VideoInfo
from .ytdlp import YtDlpError
from .pipeline import CacheError, is_same_file
from .guide_html import is_video_id, find_video_id
from .front_matter import (
    FrontMatter,
    GuideMetadata,
    FrontMatterError,
    read_front_matter,
    metadata_from_video,
    render_front_matter,
)

VideoLoader = Callable[[str], VideoInfo]


class BackfillError(Exception):
    """Raised when guides cannot be backfilled."""


@dataclass(frozen=True)
class GuideUpdate:
    """A guide whose front matter or name changes.

    Attributes:
        path (Path): The guide.
        new_path (Path): The name `guide` would give it now, in the same folder. `path` when the name does not change.
        content (bytes | None): The new file: the front matter, then the guide's Markdown as read. None when only the
            name changes.
        changed_keys (list[str]): Front matter keys that are added or get a new value. Empty when only the name, or
            only the layout of the block (such as quoting or comments), changes.
        had_front_matter (bool): Whether the guide had a front matter block.
    """

    path: Path
    new_path: Path
    content: bytes | None
    changed_keys: list[str]
    had_front_matter: bool


@dataclass(frozen=True)
class BackfillPlan:
    """The result of the first pass.

    Attributes:
        updates (list[GuideUpdate]): Guides to write or rename, in the order given.
        unchanged (list[Path]): Guides whose front matter and name would not change.
        problems (list[str]): Guides left as they are, each as `path: reason`.
    """

    updates: list[GuideUpdate]
    unchanged: list[Path]
    problems: list[str]


@dataclass(frozen=True)
class _ReadGuide:
    """A guide as read in the first pass.

    Attributes:
        path (Path): The guide.
        content (bytes): The file as read.
        front_matter (FrontMatter): Its front matter and Markdown.
        video_id (str): The video it is about.
    """

    path: Path
    content: bytes
    front_matter: FrontMatter
    video_id: str


def _read_guide(path: Path) -> _ReadGuide:
    """Read a guide and find its video.

    Args:
        path (Path): The guide.

    Returns:
        _ReadGuide: The guide as read.

    Raises:
        BackfillError: If the guide is a symbolic link, cannot be read, its front matter is invalid, or its video
            cannot be found.
    """
    # Renaming a link could replace the file it points to, if that file already has the new name.
    if path.is_symlink():
        raise BackfillError(
            f"{path}: the guide is a symbolic link; replace it with the file it points to, then run again"
        )
    try:
        content = path.read_bytes()
        # Not `read_text`, which would turn `\r\n` into `\n`: the Markdown is kept byte for byte.
        text = content.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise BackfillError(f"{path}: cannot read the guide ({exc})") from exc
    try:
        front_matter = read_front_matter(text)
    except FrontMatterError as exc:
        raise BackfillError(f"{path}: {exc}") from exc
    video_id = front_matter.metadata.video_id
    if video_id is None:
        video_id = find_video_id(front_matter.markdown)
        if video_id is None:
            raise BackfillError(f"{path}: no `video_id` in the front matter and no YouTube video link")
    elif not is_video_id(video_id):
        # The ID also names the video's cache folder, so it must not be a path such as `../x`.
        raise BackfillError(f"{path}: the front matter's `video_id` is not a YouTube video ID: {video_id!r}")
    return _ReadGuide(path=path, content=content, front_matter=front_matter, video_id=video_id)


def _build_update(guide: _ReadGuide, video: VideoInfo, timezone: datetime.tzinfo | None) -> GuideUpdate:
    """Work out a guide's new front matter and name.

    Keys that come from the video are rewritten from its metadata. `generated` is kept when set, and otherwise is the
    publish time (unknown if that is too). Keys `GuideMetadata` does not know are kept, after its keys.

    Args:
        guide (_ReadGuide): The guide as read.
        video (VideoInfo): Its video's metadata.
        timezone (datetime.tzinfo | None): Time zone of the publish day in the file name. None means the system's
            time zone.

    Returns:
        GuideUpdate: The new file and name; `content` is None when the file would not change.
    """
    old = guide.front_matter.metadata
    data = guide.front_matter.data or {}
    generated = old.generated if old.generated is not None else video.published
    metadata = metadata_from_video(video, None).model_copy(update={"generated": generated})
    # The front matter's own blank line replaces blank lines at the top of the Markdown, so a second run matches.
    markdown = guide.front_matter.markdown.lstrip("\r\n")
    content = (render_front_matter(metadata, data) + markdown).encode("utf-8")
    changed_keys = [
        name for name in GuideMetadata.model_fields if name not in data or getattr(old, name) != getattr(metadata, name)
    ]
    return GuideUpdate(
        path=guide.path,
        new_path=guide.path.with_name(guide_filename(video, timezone)),
        content=content if content != guide.content else None,
        changed_keys=changed_keys,
        had_front_matter=guide.front_matter.data is not None,
    )


def _exists(path: Path) -> bool:
    """Check whether a name is taken, by a file or by a symbolic link, even one that points nowhere.

    Args:
        path (Path): The name.

    Returns:
        bool: True if something has that name.
    """
    return path.is_symlink() or path.exists()


def _problem_list(title: str, problems: list[str]) -> str:
    """Format problems as a titled list.

    Args:
        title (str): The line above the list.
        problems (list[str]): The problems.

    Returns:
        str: The title and one `- ` line per problem.
    """
    return "\n".join([title, *(f"- {problem}" for problem in problems)])


def _stop(conflicts: list[str], problems: list[str]) -> BackfillError:
    """Build the error for problems between guides, which stop the run.

    Args:
        conflicts (list[str]): The problems between guides.
        problems (list[str]): The problems of single guides found so far, listed too so that one run reports them.

    Returns:
        BackfillError: The error, for the caller to raise.
    """
    message = _problem_list("No guide was changed, because of problems between guides:", conflicts)
    if problems:
        message += "\n" + _problem_list("These guides also have problems of their own:", problems)
    return BackfillError(message)


def plan_backfill(paths: list[Path], load_video: VideoLoader, timezone: datetime.tzinfo | None = None) -> BackfillPlan:
    """Read every guide, load its video's metadata, and work out its new front matter and name.

    Every guide is read, so one run reports all the problems found before it stops.

    Args:
        paths (list[Path]): The guides.
        load_video (VideoLoader): Returns a video's metadata from its ID, from the cache or by fetching it. It may
            raise `YtDlpError` or `CacheError`.
        timezone (datetime.tzinfo | None): Time zone of the publish day in file names. None means the system's time
            zone.

    Returns:
        BackfillPlan: The guides to write or rename, the unchanged guides, and the guides left as they are.

    Raises:
        BackfillError: If two guides are about one video, or a guide's new name is taken by another file or is also
            another guide's new name. The message lists every such problem, and the problems of single guides found
            so far. Two guides for one video are found before any metadata is loaded.
    """
    problems: list[str] = []
    guides: list[_ReadGuide] = []
    for path in paths:
        try:
            guides.append(_read_guide(path))
        except BackfillError as exc:
            problems.append(str(exc))

    by_video: dict[str, list[Path]] = {}
    for guide in guides:
        by_video.setdefault(guide.video_id, []).append(guide.path)
    duplicates = [
        f"video {video_id} has {len(found)} guides: {', '.join(str(path) for path in found)}"
        for video_id, found in by_video.items()
        if len(found) > 1
    ]
    if duplicates:
        raise _stop(duplicates, problems)

    updates: list[GuideUpdate] = []
    unchanged: list[Path] = []
    for guide in guides:
        try:
            video = load_video(guide.video_id)
        except (YtDlpError, CacheError) as exc:
            problems.append(f"{guide.path}: cannot load the metadata of video {guide.video_id} ({exc})")
            continue
        update = _build_update(guide, video, timezone)
        if update.content is None and update.new_path == update.path:
            unchanged.append(update.path)
        else:
            updates.append(update)

    taken: list[str] = []
    claimed: dict[Path, Path] = {}
    for update in updates:
        if update.new_path == update.path:
            continue
        earlier = claimed.setdefault(update.new_path, update.path)
        if earlier != update.path:
            taken.append(f"{update.path}: its new name {update.new_path.name} is also the new name of {earlier}")
        # A name that differs only in case may be the guide itself, on a case-insensitive file system.
        elif _exists(update.new_path) and not is_same_file(update.new_path, update.path):
            taken.append(f"{update.path}: its new name is taken by {update.new_path}")
    if taken:
        raise _stop(taken, problems)
    return BackfillPlan(updates=updates, unchanged=unchanged, problems=problems)


def write_content(update: GuideUpdate) -> None:
    """Write a guide's new content in place, without ever leaving half of it.

    The new file is written next to the guide, with its permissions, and then moved over it. Nothing is written when
    only the name changes.

    Args:
        update (GuideUpdate): The guide and what changes.

    Returns:
        None

    Raises:
        BackfillError: If the guide cannot be written. It is then unchanged.
    """
    if update.content is None:
        return
    temp = update.path.with_name(f".{update.path.name}.tmp")
    try:
        temp.write_bytes(update.content)
        shutil.copymode(update.path, temp)
        temp.replace(update.path)
    except OSError as exc:
        # The write error is the one to report; a leftover temporary file is harmless.
        with contextlib.suppress(OSError):
            temp.unlink(missing_ok=True)
        raise BackfillError(f"{update.path}: cannot write the guide ({exc})") from exc


def rename_guide(update: GuideUpdate) -> None:
    """Give a guide its new name, after `write_content`.

    Renaming after writing means a failed rename leaves one guide under its old name, which the next run renames.

    Args:
        update (GuideUpdate): The guide and what changes.

    Returns:
        None

    Raises:
        BackfillError: If the guide cannot be renamed, for example because the new name was taken after the first
            pass. It keeps its old name.
    """
    if update.new_path == update.path:
        return
    # Checked again: `rename` would replace a file created since the first pass.
    if _exists(update.new_path) and not is_same_file(update.new_path, update.path):
        raise BackfillError(f"{update.path}: cannot rename it, because its new name is now taken by {update.new_path}")
    try:
        update.path.rename(update.new_path)
    except OSError as exc:
        raise BackfillError(f"{update.path}: cannot rename it to {update.new_path.name} ({exc})") from exc


def describe_changes(update: GuideUpdate) -> str | None:
    """Say how a guide's front matter changes, for the report.

    Args:
        update (GuideUpdate): The guide and what changes.

    Returns:
        str | None: For example `published, generated`, or None when only the name changes.
    """
    if update.content is None:
        return None
    if not update.had_front_matter:
        return "front matter added"
    if not update.changed_keys:
        return "front matter reformatted, no value changed"
    return ", ".join(update.changed_keys)
