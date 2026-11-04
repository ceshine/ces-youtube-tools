"""Find the guides already written for a video, and decide which ones a new guide replaces."""

from pathlib import Path
from dataclasses import dataclass

from .guide import GuideError
from .guide_html import is_markdown, find_video_id
from .front_matter import FrontMatterError, split_front_matter


@dataclass(frozen=True)
class GuideScan:
    """The files already about one video in a folder.

    Attributes:
        video_id (str): The video.
        paths (list[Path]): Guides whose front matter names the video, in name order. Only these may be replaced.
        linked (list[Path]): Files about the video only by their links (no front matter `video_id`, or invalid front
            matter), in name order. They may not be guides, so they are never replaced.
        warnings (list[str]): Files that could not be read (skipped), or whose front matter is invalid (judged by
            their links).
    """

    video_id: str
    paths: list[Path]
    linked: list[Path]
    warnings: list[str]


def find_guides_for_video(output_dir: Path, video_id: str) -> GuideScan:
    """Find the files about a video among the `.md` files in a folder, whatever their names.

    A file's video is its front matter's `video_id`, else the video its links point to most, as in `site`. A file
    that cannot be read never stops a guide from being written; it is only reported.

    Args:
        output_dir (Path): The folder. One that does not exist holds no guides.
        video_id (str): YouTube video ID.

    Returns:
        GuideScan: The files about the video, by how they were found, and the files that could not be checked
            properly.
    """
    paths: list[Path] = []
    linked: list[Path] = []
    warnings: list[str] = []
    if not output_dir.is_dir():
        return GuideScan(video_id=video_id, paths=paths, linked=linked, warnings=warnings)
    try:
        files = sorted(child for child in output_dir.iterdir() if is_markdown(child) and child.is_file())
    except OSError as exc:
        warnings.append(f"Cannot list {output_dir} ({exc}), so it was not checked for guides.")
        return GuideScan(video_id=video_id, paths=paths, linked=linked, warnings=warnings)
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            warnings.append(f"Cannot read {path} ({exc}), so it was not checked for a guide of this video.")
            continue
        try:
            metadata, markdown = split_front_matter(text)
        except FrontMatterError as exc:
            warnings.append(f"{path}: {exc}. Its links were used to find its video.")
            metadata, markdown = None, text
        if metadata is not None and metadata.video_id is not None:
            if metadata.video_id == video_id:
                paths.append(path)
        elif find_video_id(markdown) == video_id:
            linked.append(path)
    return GuideScan(video_id=video_id, paths=paths, linked=linked, warnings=warnings)


def is_same_file(first: Path, second: Path) -> bool:
    """Check whether two paths name one file, as through a symbolic link or a case-insensitive file system.

    Args:
        first (Path): A path.
        second (Path): Another path.

    Returns:
        bool: True if both exist and are the same file.
    """
    try:
        return first.samefile(second)
    except OSError:
        return False


def guides_to_replace(path: Path, scan: GuideScan) -> list[Path]:
    """List the files a new guide at `path` would replace.

    Args:
        path (Path): Where the new guide goes.
        scan (GuideScan): The files about the video in its folder, from `find_guides_for_video`.

    Returns:
        list[Path]: The file at `path` if it exists, then the older guides of the video under other names.

    Raises:
        GuideError: If a file is about the video only by its links. It may not be a guide, so it is never replaced,
            even with `--force`.
    """
    linked = [old for old in scan.linked if not is_same_file(old, path)]
    if linked:
        names = ", ".join(str(old) for old in linked)
        raise GuideError(
            f"{names} links to this video, but its front matter does not name it, so it is not replaced, even with "
            "--force. Remove or move it, or add `video_id` to its front matter, then run again."
        )
    others = [old for old in scan.paths if old != path and not is_same_file(old, path)]
    return ([path] if path.exists() else []) + others


def check_existing_guides(path: Path, scan: GuideScan, force: bool) -> list[Path]:
    """Refuse to write a guide over an existing one, unless forced.

    Args:
        path (Path): Where the new guide goes.
        scan (GuideScan): The files about the video in its folder, from `find_guides_for_video`.
        force (bool): Allow overwriting the file at `path` and replacing the older guides.

    Returns:
        list[Path]: The older guides under other names, to remove once the new guide is written.

    Raises:
        GuideError: If the file at `path` exists or the video has older guides and `force` is not set, or a file is
            about the video only by its links.
    """
    replaced = guides_to_replace(path, scan)
    if path.exists() and not force:
        raise GuideError(f"{path} already exists. Use --force to overwrite it.")
    others = [old for old in replaced if old != path]
    if others and not force:
        names = ", ".join(str(old) for old in others)
        raise GuideError(f"Video {scan.video_id} already has a guide: {names}. Use --force to replace it.")
    return others
