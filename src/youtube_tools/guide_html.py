"""Convert a Markdown watching guide into HTML."""

import re
import logging
from urllib.parse import parse_qs, urlparse
from collections import Counter

from markdown_it import MarkdownIt

LOGGER = logging.getLogger(__name__)

_TITLE_PREFIX = "Watching Guide:"
_LINK_URL = re.compile(r"\]\((<[^>]*>|[^)\s]*)")
_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")


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
        if _VIDEO_ID.fullmatch(candidate):
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
