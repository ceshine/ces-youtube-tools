"""Convert a Markdown watching guide into HTML."""

import re
import logging
from html import escape
from string import Template
from urllib.parse import parse_qs, urlparse
from pathlib import Path
from importlib import resources
from collections import Counter

from markdown_it import MarkdownIt

LOGGER = logging.getLogger(__name__)

_TITLE_PREFIX = "Watching Guide:"
_LINK_URL = re.compile(r"\]\((<[^>]*>|[^)\s]*)")
_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
# The embedded player treats a viewer whose browser blocks YouTube's cookies in the iframe as a non-member.
_MEMBERS_HINT = (
    '  <p class="members-hint">This is a members-only video. If the player asks you to join, your browser is not '
    "sending your YouTube sign-in to it. In Firefox, turn off Enhanced Tracking Protection for this page (the shield "
    "icon in the address bar); other browsers may need third-party cookies allowed for this site. Until then, "
    "timestamps still open YouTube when you Ctrl+click them.</p>\n"
)
_INDEX_LINK = '  <nav class="site-nav"><a href="./">&larr; All guides</a></nav>\n'


class GuideHtmlError(Exception):
    """Raised when a guide cannot be turned into an HTML page."""


def markdown_to_html(markdown: str) -> str:
    """Convert Markdown to an HTML fragment.

    Raw HTML in the Markdown is not passed through: it is shown as text. Tables are supported, and quotes and dashes
    are made typographic.

    Args:
        markdown (str): The guide.

    Returns:
        str: The HTML body, without `<html>` or `<body>` elements.
    """
    parser = MarkdownIt("commonmark", {"html": False, "typographer": True}).enable(
        ["table", "replacements", "smartquotes"]
    )
    return parser.render(markdown)


def is_video_id(value: str) -> bool:
    """Check that a value is a YouTube video ID: 11 characters from `A-Z`, `a-z`, `0-9`, `_` and `-`.

    Args:
        value (str): The value, such as a front matter `video_id`.

    Returns:
        bool: True if it is a video ID.
    """
    return _VIDEO_ID.fullmatch(value) is not None


def find_video_id(markdown: str) -> str | None:
    """Find the video a guide is about from its YouTube links.

    Args:
        markdown (str): The guide.

    Returns:
        str | None: The `v=` value (or `youtu.be` path) that appears in the most links, or None if no link has a
            valid video ID.
    """
    counts: Counter[str] = Counter()
    for raw_url in _LINK_URL.findall(markdown):
        try:
            url = urlparse(raw_url.strip("<>"))
        except ValueError:
            LOGGER.debug("Skipped a malformed link target: %r", raw_url)
            continue
        if url.hostname not in _YOUTUBE_HOSTS:
            continue
        if url.hostname == "youtu.be":
            candidate = url.path.lstrip("/")
        else:
            candidate = parse_qs(url.query).get("v", [""])[0]
        if is_video_id(candidate):
            counts[candidate] += 1
    if not counts:
        return None
    return counts.most_common(1)[0][0]


def guide_title(markdown: str, fallback: str) -> str:
    """Get the page title from the guide's first level-one heading.

    Args:
        markdown (str): The guide.
        fallback (str): The title to use when the guide has no such heading, such as the file name.

    Returns:
        str: The heading text without the "Watching Guide:" prefix.
    """
    tokens = MarkdownIt("commonmark").parse(markdown)
    for index, token in enumerate(tokens):
        if token.type == "heading_open" and token.tag == "h1":
            title = tokens[index + 1].content.strip()
            if title.startswith(_TITLE_PREFIX):
                title = title[len(_TITLE_PREFIX) :].strip()
            return title or fallback
    return fallback


def render_guide_page(
    markdown: str, video_id: str, fallback_title: str, members_only: bool = False, link_to_index: bool = False
) -> str:
    """Build the full HTML page for a guide, with the video's player embedded.

    Args:
        markdown (str): The guide, without front matter.
        video_id (str): The YouTube video ID. It is put into a URL and a JavaScript string without escaping, so it
            must be 11 characters from `A-Z`, `a-z`, `0-9`, `_` and `-`.
        fallback_title (str): The page title to use when the guide has no level-one heading.
        members_only (bool): Add a hint under the player about letting the browser send the YouTube sign-in.
        link_to_index (bool): Add a link to the index (`./`) above the player, for pages of the site.

    Returns:
        str: The complete page.

    Raises:
        GuideHtmlError: If `video_id` is not a valid video ID.
    """
    if not is_video_id(video_id):
        raise GuideHtmlError(f"Not a valid YouTube video ID: {video_id!r}")
    template = Template(resources.files("ces_youtube_tools").joinpath("templates/guide_page.html").read_text("utf-8"))
    return template.substitute(
        title=escape(guide_title(markdown, fallback_title)),
        video_id=video_id,
        body=markdown_to_html(markdown),
        members_hint=_MEMBERS_HINT if members_only else "",
        index_link=_INDEX_LINK if link_to_index else "",
    )


def expand_paths(paths: list[str], cwd: Path) -> list[Path]:
    """Collect the Markdown guides named by files and folders.

    A folder stands for the `.md` files directly inside it (not in its subfolders). A file that is not a `.md` file
    is ignored.

    Args:
        paths (list[str]): Files and folders. Relative ones are taken from `cwd`; `~` is expanded.
        cwd (Path): The directory relative paths start from.

    Returns:
        list[Path]: The Markdown files, folders in name order, each file once.

    Raises:
        GuideHtmlError: If a path does not exist, a folder cannot be read or holds no Markdown file, or no Markdown
            file is left at all (for example, only non-Markdown files were given).
    """
    found: dict[Path, None] = {}
    for raw in paths:
        path = cwd / Path(raw).expanduser()
        if path.is_dir():
            try:
                files = sorted(child for child in path.iterdir() if is_markdown(child) and child.is_file())
            except OSError as exc:
                raise GuideHtmlError(f"Cannot read {raw!r} ({exc})") from exc
            if not files:
                raise GuideHtmlError(f"No Markdown files in {raw!r}")
            found.update(dict.fromkeys(files))
        elif path.is_file():
            if is_markdown(path):
                found[path] = None
        else:
            raise GuideHtmlError(f"No such file or folder: {raw!r}")
    if not found:
        raise GuideHtmlError("No Markdown files among the given paths")
    return list(found)


def is_markdown(path: Path) -> bool:
    """Check whether a path names a Markdown file, by its suffix.

    Args:
        path (Path): The path.

    Returns:
        bool: True for a `.md` suffix, in any case.
    """
    return path.suffix.lower() == ".md"
