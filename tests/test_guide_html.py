from pathlib import Path

import pytest

from youtube_tools.guide_html import guide_title, find_video_id, markdown_to_html

FIXTURE = Path(__file__).parent / "fixtures" / "sample_guide.md"


@pytest.fixture
def guide() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def test_markdown_to_html_renders_structure(guide: str) -> None:
    html = markdown_to_html(guide)
    assert "<h1>" in html
    assert "<h2>Opening</h2>" in html
    assert "<li>" in html
    assert "<table>" in html
    assert 'href="https://www.youtube.com/watch?v=abcdefghijk&amp;t=45s"' in html


def test_markdown_to_html_escapes_raw_html(guide: str) -> None:
    html = markdown_to_html(guide)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_markdown_to_html_drops_javascript_links(guide: str) -> None:
    html = markdown_to_html(guide)
    assert 'href="javascript:' not in html
    assert "bad link" in html


def test_markdown_to_html_uses_typographic_quotes_and_dashes(guide: str) -> None:
    html = markdown_to_html(guide)
    assert "“short”" in html
    assert "–" in html


def test_find_video_id_picks_the_most_common(guide: str) -> None:
    assert find_video_id(guide) == "abcdefghijk"


def test_find_video_id_accepts_short_links() -> None:
    assert find_video_id("[x](https://youtu.be/abcdefghijk?t=5)") == "abcdefghijk"


def test_find_video_id_ignores_other_hosts_and_invalid_ids() -> None:
    markdown = "[a](https://example.com/watch?v=abcdefghijk) [b](https://www.youtube.com/watch?v=short)"
    assert find_video_id(markdown) is None


def test_find_video_id_without_links() -> None:
    assert find_video_id("# Title\n\nNo links.") is None


def test_guide_title_strips_prefix(guide: str) -> None:
    assert guide_title(guide, "fallback") == 'A "short" talk -- about testing'


def test_guide_title_falls_back() -> None:
    assert guide_title("No heading here.\n\n## Not level one", "My file") == "My file"
    assert guide_title("# Watching Guide:\n", "My file") == "My file"


def test_guide_title_ignores_headings_in_code_fences() -> None:
    markdown = "```sh\n# a shell comment\n```\n\n# Real title\n"
    assert guide_title(markdown, "My file") == "Real title"


def test_find_video_id_skips_malformed_links() -> None:
    markdown = "[x](https://[abc) [y](https://www.youtube.com/watch?v=abcdefghijk&t=5s)"
    assert find_video_id(markdown) == "abcdefghijk"
