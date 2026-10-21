"""Build the guides into a static site for Cloudflare Pages.

The work is done in two passes, as in `backfill`. The first reads and renders every guide in memory; if anything
fails there, the output folder is not touched. The second clears the output folder and writes the site.
"""

import shutil
import datetime
from html import escape
from string import Template
from pathlib import Path
from importlib import resources
from dataclasses import dataclass

from .formatting import format_duration
from .guide_html import guide_title, find_video_id, GuideHtmlError, render_guide_page
from .front_matter import GuideMetadata, FrontMatterError, split_front_matter

# Written into the output folder so a later build knows the folder is its own and may be cleared.
SITE_MARKER = ".ces-youtube-tools-site"
_MARKER_TEXT = "Written by `ces-youtube-tools site`. Everything in this folder is deleted on the next build.\n"
_ROBOTS_TXT = "User-agent: *\nDisallow: /\n"
# The page must send its origin as the Referer, or the embedded player fails with Error 153, so the referrer policy
# is the browsers' default, stated here so a host default cannot change it.
_HEADERS = "/*\n  X-Robots-Tag: noindex, nofollow\n  Referrer-Policy: strict-origin-when-cross-origin\n"


class SiteError(Exception):
    """Raised when the site cannot be built."""


@dataclass(frozen=True)
class SitePage:
    """A guide rendered as a page of the site.

    Attributes:
        source (Path): The guide.
        video_id (str): The video, which also names the page (`<video_id>.html`).
        title (str): The title for the index: the YouTube title, else the guide's heading, else the file name.
        metadata (GuideMetadata): The guide's front matter.
        html (str): The page.
    """

    source: Path
    video_id: str
    title: str
    metadata: GuideMetadata
    html: str

    @property
    def filename(self) -> str:
        """str: The page's file name in the site."""
        return f"{self.video_id}.html"


@dataclass(frozen=True)
class SiteBuild:
    """The result of the first pass.

    Attributes:
        pages (list[SitePage]): The pages to publish, newest upload first.
        left_out (list[Path]): Guides left out because they are members-only or their availability is unknown.
    """

    pages: list[SitePage]
    left_out: list[Path]


def is_publishable(metadata: GuideMetadata, include_members_only: bool) -> bool:
    """Decide whether a guide goes into the site.

    Args:
        metadata (GuideMetadata): The guide's front matter. A guide without front matter has every field unknown.
        include_members_only (bool): Publish members-only guides too.

    Returns:
        bool: True if the guide is known not to be members-only, or members-only guides are included. A guide whose
            availability is unknown counts as members-only.
    """
    return include_members_only or metadata.members_only is False


def collect_pages(paths: list[Path], include_members_only: bool) -> SiteBuild:
    """Read and render every guide that goes into the site.

    Every guide is tried, so one run reports all the problems.

    Args:
        paths (list[Path]): The guides.
        include_members_only (bool): Publish members-only guides, and guides whose availability is unknown, too.

    Returns:
        SiteBuild: The pages, newest upload first, and the guides left out.

    Raises:
        SiteError: If any guide cannot be read, has invalid front matter, has no video ID, or is about the same
            video as another guide. The message lists every such guide.
    """
    pages: list[SitePage] = []
    left_out: list[Path] = []
    problems: list[str] = []
    for path in paths:
        try:
            metadata, markdown = split_front_matter(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            problems.append(f"{path}: cannot read the guide ({exc})")
            continue
        except FrontMatterError as exc:
            problems.append(f"{path}: {exc}")
            continue
        if not is_publishable(metadata, include_members_only):
            left_out.append(path)
            continue
        video_id = metadata.video_id or find_video_id(markdown)
        if video_id is None:
            problems.append(f"{path}: no YouTube video link found, so the video is unknown")
            continue
        try:
            html = render_guide_page(markdown, video_id, path.stem, metadata.members_only is True, link_to_index=True)
        except GuideHtmlError as exc:
            problems.append(f"{path}: {exc}")
            continue
        title = metadata.title or guide_title(markdown, path.stem)
        pages.append(SitePage(source=path, video_id=video_id, title=title, metadata=metadata, html=html))
    problems.extend(_duplicate_videos(pages))
    if problems:
        details = "\n".join(f"- {problem}" for problem in problems)
        count = "1 problem" if len(problems) == 1 else f"{len(problems)} problems"
        raise SiteError(f"{count} found, so the site was not built:\n{details}")
    return SiteBuild(pages=_newest_first(pages), left_out=left_out)


def _duplicate_videos(pages: list[SitePage]) -> list[str]:
    sources: dict[str, list[Path]] = {}
    for page in pages:
        sources.setdefault(page.video_id, []).append(page.source)
    return [
        f"video {video_id} has more than one guide, keep one: " + ", ".join(str(path) for path in paths)
        for video_id, paths in sources.items()
        if len(paths) > 1
    ]


def _newest_first(pages: list[SitePage]) -> list[SitePage]:
    # Two stable sorts: titles in order within a day, and guides with an unknown date last.
    by_title = sorted(pages, key=lambda page: page.title.casefold())
    return sorted(by_title, key=lambda page: page.metadata.upload_date or datetime.date.min, reverse=True)


def render_index(pages: list[SitePage]) -> str:
    """Build the index page.

    Args:
        pages (list[SitePage]): The pages, in the order to list them.

    Returns:
        str: The complete page.
    """
    template = Template(resources.files("ces_youtube_tools").joinpath("templates/site_index.html").read_text("utf-8"))
    if pages:
        items = "\n".join(_index_item(page) for page in pages)
        listing = f'<ol class="guides">\n{items}\n</ol>'
    else:
        listing = '<p class="empty">No guides yet.</p>'
    count = "1 guide" if len(pages) == 1 else f"{len(pages)} guides"
    return template.substitute(count=count, listing=listing)


def _index_item(page: SitePage) -> str:
    metadata = page.metadata
    details: list[str] = []
    if metadata.upload_date is not None:
        details.append(f'<time datetime="{metadata.upload_date}">{metadata.upload_date}</time>')
    if metadata.channel:
        details.append(f'<span class="channel">{escape(metadata.channel)}</span>')
    if metadata.duration is not None:
        details.append(f'<span class="length">{format_duration(metadata.duration)}</span>')
    if metadata.members_only is True:
        details.append('<span class="badge">Members only</span>')
    elif metadata.members_only is None:
        details.append('<span class="badge">Availability unknown</span>')
    return f'<li><a href="{page.video_id}">{escape(page.title)}</a>\n<p class="meta">{" ".join(details)}</p></li>'


def write_site(build: SiteBuild, out_dir: Path) -> None:
    """Write the site into a folder, replacing what an earlier build wrote there.

    Args:
        build (SiteBuild): The pages. Its guides, published or left out, are the files the output folder may not
            hold.
        out_dir (Path): The output folder; `~` is expanded. It must be missing, empty, or a folder an earlier build
            wrote, and may not hold any of the guides, directly or in a subfolder.

    Returns:
        None

    Raises:
        SiteError: If the output folder cannot be used, cleared or written.
    """
    out_dir = out_dir.expanduser()
    _prepare_out_dir(out_dir, [page.source for page in build.pages] + build.left_out)
    files = {page.filename: page.html for page in build.pages}
    files.update({"index.html": render_index(build.pages), "robots.txt": _ROBOTS_TXT, "_headers": _HEADERS})
    for name, content in files.items():
        try:
            (out_dir / name).write_text(content, encoding="utf-8")
        except OSError as exc:
            raise SiteError(
                f"Cannot write {out_dir / name} ({exc}). The output folder is incomplete; build again before deploying."
            ) from exc


def _prepare_out_dir(out_dir: Path, guides: list[Path]) -> None:
    out = out_dir.resolve()
    for guide in guides:
        # Both where the guide is listed and, for a symbolic link, the file it points to.
        locations = {guide.expanduser().parent.resolve() / guide.name, guide.expanduser().resolve()}
        if any(out in location.parents for location in locations):
            raise SiteError(f"The output folder {out_dir} holds the guide {guide}; choose a folder outside the guides")
    if out.exists() and not out.is_dir():
        raise SiteError(f"{out_dir} is not a folder")
    try:
        if out.is_dir():
            children = list(out.iterdir())
            if children and not (out / SITE_MARKER).is_file():
                raise SiteError(
                    f"{out_dir} is not empty and was not written by `site`, so it is not cleared. "
                    + "Choose a new or empty folder."
                )
            for child in children:
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
        out.mkdir(parents=True, exist_ok=True)
        (out / SITE_MARKER).write_text(_MARKER_TEXT, encoding="utf-8")
    except OSError as exc:
        raise SiteError(f"Cannot prepare the output folder {out_dir} ({exc})") from exc
