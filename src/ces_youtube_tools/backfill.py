"""Add front matter to guides written before guides had it.

The work is done in two passes. The first reads every guide and loads its video's metadata; if anything fails there,
no guide is changed. The second writes the front matter, one guide at a time.
"""

import shutil
import contextlib
from pathlib import Path
from dataclasses import dataclass
from collections.abc import Callable

from .video import VideoInfo
from .ytdlp import YtDlpError
from .pipeline import CacheError
from .guide_html import find_video_id
from .front_matter import has_front_matter, metadata_from_video, render_front_matter

VideoLoader = Callable[[str], VideoInfo]


class BackfillError(Exception):
    """Raised when guides cannot be backfilled."""


@dataclass(frozen=True)
class PendingGuide:
    """A guide that needs front matter, with what is needed to write it.

    Attributes:
        path (Path): The guide.
        content (bytes): The guide as read, kept byte for byte below the new front matter.
        video (VideoInfo): The video the guide is about.
    """

    path: Path
    content: bytes
    video: VideoInfo


@dataclass(frozen=True)
class BackfillPlan:
    """The result of the first pass.

    Attributes:
        pending (list[PendingGuide]): Guides to write, in the order given.
        skipped (list[Path]): Guides that already have front matter (valid or not), which are left alone.
    """

    pending: list[PendingGuide]
    skipped: list[Path]


def plan_backfill(paths: list[Path], load_video: VideoLoader) -> BackfillPlan:
    """Read every guide and load the metadata of the videos of those without front matter.

    Every guide is tried, so one run reports all the problems.

    Args:
        paths (list[Path]): The guides.
        load_video (VideoLoader): Returns a video's metadata from its ID, from the cache or by fetching it. It may
            raise `YtDlpError` or `CacheError`.

    Returns:
        BackfillPlan: The guides to write and the guides skipped.

    Raises:
        BackfillError: If any guide cannot be read, has no video link, or its video's metadata cannot be loaded. The
            message lists every such guide.
    """
    pending: list[PendingGuide] = []
    skipped: list[Path] = []
    problems: list[str] = []
    for path in paths:
        try:
            content = path.read_bytes()
            text = content.decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            problems.append(f"{path}: cannot read the guide ({exc})")
            continue
        if has_front_matter(text):
            skipped.append(path)
            continue
        video_id = find_video_id(text)
        if video_id is None:
            problems.append(f"{path}: no YouTube video link found, so the video is unknown")
            continue
        try:
            video = load_video(video_id)
        except (YtDlpError, CacheError) as exc:
            problems.append(f"{path}: cannot load the metadata of video {video_id} ({exc})")
            continue
        pending.append(PendingGuide(path=path, content=content, video=video))
    if problems:
        details = "\n".join(f"- {problem}" for problem in problems)
        raise BackfillError(
            f"{len(problems)} of {len(paths)} guides cannot be backfilled, so none was changed:\n{details}"
        )
    return BackfillPlan(pending=pending, skipped=skipped)


def write_front_matter(guide: PendingGuide) -> None:
    """Put front matter at the top of a guide, leaving the rest of the file unchanged.

    The new file is written next to the guide and then moved over it, so a failed write never leaves half a guide.
    If the guide is a symbolic link, the file it points to is the one changed, and the link is kept.
    The `generated` date is left unknown, because nothing recorded when the guide was written.

    Args:
        guide (PendingGuide): The guide and its video.

    Returns:
        None

    Raises:
        BackfillError: If the guide cannot be written. The guide is then unchanged.
    """
    front_matter = render_front_matter(metadata_from_video(guide.video, None)).encode("utf-8")
    # Moving the new file over a link would replace the link, so write next to the file it points to instead.
    target = guide.path.resolve()
    temp = target.with_name(f".{target.name}.tmp")
    try:
        temp.write_bytes(front_matter + guide.content)
        shutil.copymode(target, temp)
        temp.replace(target)
    except OSError as exc:
        # The write error is the one to report; a leftover temporary file is harmless.
        with contextlib.suppress(OSError):
            temp.unlink(missing_ok=True)
        raise BackfillError(f"{guide.path}: cannot write the guide ({exc})") from exc
