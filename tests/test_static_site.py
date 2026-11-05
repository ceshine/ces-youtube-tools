import re
import json
import datetime
from pathlib import Path
from importlib import resources

import pytest

from ces_youtube_tools.static_site import (
    SitePage,
    SiteError,
    write_site,
    SITE_MARKER,
    render_index,
    collect_pages,
    index_payload,
    is_publishable,
)
from ces_youtube_tools.front_matter import GuideMetadata

from .conftest import write_guide, write_sample_guides

PAYLOAD_KEYS = {"video_id", "title", "channel", "upload_date", "published", "duration", "members_only", "generated"}


@pytest.fixture
def guides(tmp_path: Path) -> Path:
    return write_sample_guides(tmp_path)


def guide_paths(folder: Path) -> list[Path]:
    return sorted(folder.glob("*.md"))


def guides_block(index: str) -> object:
    """Read the payload from an index page, as the browser would: up to the first `</script>`."""
    found = re.search(r'<script type="application/json" id="guides">(.*?)</script>', index, re.DOTALL)
    assert found is not None
    return json.loads(found.group(1))


@pytest.mark.parametrize(
    ("members_only", "include", "expected"),
    [(False, False, True), (True, False, False), (None, False, False), (True, True, True), (None, True, True)],
)
def test_is_publishable(members_only: bool | None, include: bool, expected: bool) -> None:
    assert is_publishable(GuideMetadata(members_only=members_only), include) is expected


def test_collect_pages_leaves_out_members_only_and_unknown(guides: Path) -> None:
    build = collect_pages(guide_paths(guides), include_members_only=False)
    assert [page.video_id for page in build.pages] == ["bbbbbbbbbbb", "aaaaaaaaaaa"]
    assert sorted(path.name for path in build.left_out) == ["members.md", "plain.md", "unknown.md"]
    page = build.pages[0]
    assert (page.title, page.filename, page.source) == ("New", "bbbbbbbbbbb.html", guides / "new.md")
    assert '<a href="./">' in page.html
    assert "members-hint" not in page.html.split("<body>")[1]
    assert "video_id:" not in page.html


def test_collect_pages_says_what_wrote_each_guide(guides: Path) -> None:
    _ = write_guide(
        guides,
        "old.md",
        "aaaaaaaaaaa",
        title="Old",
        upload_date="2026-10-01",
        members_only=False,
        harness="Claude Code",
        model="claude-sonnet-5-5",
        effort="high",
    )
    build = collect_pages(guide_paths(guides), include_members_only=False)
    pages = {page.video_id: page.html for page in build.pages}
    line = '<p class="written-by">Written by Claude Code with claude-sonnet-5-5 (high effort).</p>'
    assert line in pages["aaaaaaaaaaa"]
    # A guide written before the tool recorded it has no line.
    assert 'class="written-by"' not in pages["bbbbbbbbbbb"].split("<body>")[1]


def test_collect_pages_includes_members_only_when_asked(guides: Path) -> None:
    build = collect_pages(guide_paths(guides), include_members_only=True)
    # Newest upload first; guides without a date last, in title order (the heading stands in for a missing title).
    assert [page.title for page in build.pages] == ["Members", "New", "Old", "The heading", "Unknown"]
    assert build.left_out == []
    assert '<p class="members-hint">' in build.pages[0].html.split("<body>")[1]


def test_collect_pages_reports_every_problem(guides: Path) -> None:
    (guides / "nolinks.md").write_text("---\nmembers_only: false\n---\n# No links\n", encoding="utf-8")
    (guides / "badyaml.md").write_text("---\ntitle: a: b\n---\n# Bad\n", encoding="utf-8")
    (guides / "binary.md").write_bytes(b"\xff\xfe")
    write_guide(guides, "again.md", "aaaaaaaaaaa", members_only=False)
    with pytest.raises(SiteError) as info:
        _ = collect_pages(guide_paths(guides), include_members_only=False)
    message = str(info.value)
    assert message.startswith("4 problems found, so the site was not built:\n")
    assert "nolinks.md: no YouTube video link found" in message
    assert "badyaml.md: the front matter is not valid YAML at line 2" in message
    assert "binary.md: cannot read the guide" in message
    assert "video aaaaaaaaaaa has more than one guide" in message


def test_collect_pages_checks_the_front_matter_of_guides_it_leaves_out(guides: Path) -> None:
    (guides / "badmembers.md").write_text("---\nmembers_only: maybe\n---\n# Bad\n", encoding="utf-8")
    with pytest.raises(SiteError, match="badmembers.md: invalid front matter"):
        _ = collect_pages(guide_paths(guides), include_members_only=False)


def test_render_index_leaves_the_list_to_the_script() -> None:
    def page(video_id: str, title: str) -> SitePage:
        metadata = GuideMetadata(channel="A & B", upload_date=datetime.date(2026, 10, 8), members_only=False)
        return SitePage(source=Path("x.md"), video_id=video_id, title=title, metadata=metadata, html="")

    pages = [page("aaaaaaaaaaa", "<Title>"), page("bbbbbbbbbbb", "Two")]
    index = render_index(pages)
    assert '<p class="count" id="count" aria-live="polite">2 guides.</p>' in index
    assert '<script type="module" src="index.mjs"></script>' in index
    assert "<noscript>" in index
    assert '<div class="controls" id="controls" hidden>' in index  # shown by the script
    for name in ("sort", "order", "group", "show"):
        assert f'<div class="segmented" id="{name}-choices" role="radiogroup"' in index  # filled by the script
    assert '<div class="choice" id="show-row" hidden>' in index  # shown by the script when it can read done marks
    assert '<button type="button" id="reset">Reset view</button>' in index
    assert "<ol" not in index and "<Title>" not in index
    assert guides_block(index) == index_payload(pages)
    assert "$" not in index

    assert '<p class="count" id="count" aria-live="polite">1 guide.</p>' in render_index(pages[:1])


def test_render_index_without_guides() -> None:
    index = render_index([])
    assert '<p class="count" id="count" aria-live="polite">0 guides.</p>' in index
    assert guides_block(index) == []


def test_index_payload_has_only_what_the_index_shows() -> None:
    metadata = GuideMetadata.model_validate(
        {
            "video_id": "aaaaaaaaaaa",
            "title": "YouTube title",
            "channel": "A channel",
            "channel_id": "UC123",
            "upload_date": "2026-10-08",
            "published": "2026-10-09T00:30:00+08:00",
            "duration": 3725,
            "members_only": False,
            "generated": "2026-10-01",
        }
    )
    page = SitePage(source=Path("x.md"), video_id="aaaaaaaaaaa", title="Shown title", metadata=metadata, html="<p>")
    assert index_payload([page]) == [
        {
            "video_id": "aaaaaaaaaaa",
            "title": "Shown title",
            "channel": "A channel",
            "upload_date": "2026-10-08",
            "published": "2026-10-08T16:30:00Z",
            "duration": 3725,
            "members_only": False,
            "generated": "2026-10-01",
        }
    ]

    unknown = SitePage(source=Path("y.md"), video_id="bbbbbbbbbbb", title="Two", metadata=GuideMetadata(), html="")
    assert index_payload([unknown])[0] == {
        "video_id": "bbbbbbbbbbb",
        "title": "Two",
        "channel": None,
        "upload_date": None,
        "published": None,
        "duration": None,
        "members_only": None,
        "generated": None,
    }

    written = GuideMetadata.model_validate({"generated": "2026-10-14T18:03:12+02:00"})
    page = SitePage(source=Path("z.md"), video_id="ccccccccccc", title="Three", metadata=written, html="")
    assert index_payload([page])[0]["generated"] == "2026-10-14T16:03:12Z"


def test_index_payload_keeps_a_title_that_would_end_the_script() -> None:
    title = '</script><script>alert("x")</script> & <!--'
    page = SitePage(source=Path("x.md"), video_id="aaaaaaaaaaa", title=title, metadata=GuideMetadata(), html="")
    index = render_index([page])
    assert "</script><script>" not in index
    assert guides_block(index) == index_payload([page])
    assert index_payload([page])[0]["title"] == title


def test_index_payload_is_in_the_page_without_left_out_guides(guides: Path, tmp_path: Path) -> None:
    out = tmp_path / "site"
    write_site(collect_pages(guide_paths(guides), include_members_only=False), out)
    index = (out / "index.html").read_text(encoding="utf-8")
    payload = guides_block(index)
    assert isinstance(payload, list)
    assert [entry["video_id"] for entry in payload] == ["bbbbbbbbbbb", "aaaaaaaaaaa"]
    for left_out in ["ccccccccccc", "ddddddddddd", "eeeeeeeeeee"]:
        assert left_out not in index
    assert all(set(entry) == PAYLOAD_KEYS for entry in payload)


def test_write_site_writes_every_file(guides: Path, tmp_path: Path) -> None:
    out = tmp_path / "site" / "nested"
    write_site(collect_pages(guide_paths(guides), include_members_only=False), out)
    assert sorted(child.name for child in out.iterdir()) == [
        SITE_MARKER,
        "_headers",
        "aaaaaaaaaaa.html",
        "bbbbbbbbbbb.html",
        "index.html",
        "index.mjs",
        "robots.txt",
        "site_done.mjs",
    ]
    templates = resources.files("ces_youtube_tools").joinpath("templates")
    assert (out / "index.mjs").read_text(encoding="utf-8") == templates.joinpath("site_index.mjs").read_text("utf-8")
    assert (out / "site_done.mjs").read_text(encoding="utf-8") == templates.joinpath("site_done.mjs").read_text("utf-8")
    assert (out / "robots.txt").read_text(encoding="utf-8") == "User-agent: *\nDisallow: /\n"
    headers = (out / "_headers").read_text(encoding="utf-8")
    assert headers.startswith("/*\n") and "  X-Robots-Tag: noindex, nofollow\n" in headers
    assert "no-referrer" not in headers  # the player needs the Referer


def test_write_site_writes_every_module_a_script_imports(guides: Path, tmp_path: Path) -> None:
    # The Node tests import the scripts from `templates/`, where `index.mjs` is `site_index.mjs`, so only the written
    # site shows whether a file written under another name breaks a relative import. A guide page's imports are in its
    # module scripts, which are read alone, so an import shown in a guide's code span is not taken for one.
    out = tmp_path / "site"
    write_site(collect_pages(guide_paths(guides), include_members_only=False), out)
    scripts = [(script.name, script.read_text("utf-8")) for script in out.glob("*.mjs")]
    for page in out.glob("*.html"):
        found = re.findall(r'<script type="module">(.*?)</script>', page.read_text("utf-8"), re.DOTALL)
        scripts.extend((page.name, code) for code in found)
    imports = {
        (name, target)
        for name, code in scripts
        for target in re.findall(r"""\b(?:from|import)\s*["'](\.{1,2}/[^"']+)["']""", code)
    }
    assert ("index.mjs", "./site_done.mjs") in imports
    assert ("aaaaaaaaaaa.html", "./site_done.mjs") in imports
    missing = [(script, target) for script, target in imports if not (out / target).is_file()]
    assert missing == []


def test_write_site_clears_its_own_folder(guides: Path, tmp_path: Path) -> None:
    out = tmp_path / "site"
    write_site(collect_pages(guide_paths(guides), include_members_only=True), out)
    (out / "assets").mkdir()
    (out / "assets" / "stale.css").write_text("", encoding="utf-8")
    write_site(collect_pages(guide_paths(guides), include_members_only=False), out)
    assert not (out / "ccccccccccc.html").exists()
    assert not (out / "assets").exists()
    assert (out / "aaaaaaaaaaa.html").is_file()


def test_write_site_does_not_follow_a_symlink_when_clearing(guides: Path, tmp_path: Path) -> None:
    out = tmp_path / "site"
    write_site(collect_pages(guide_paths(guides), include_members_only=False), out)
    keep = tmp_path / "keep"
    keep.mkdir()
    (keep / "file.txt").write_text("keep", encoding="utf-8")
    (out / "link").symlink_to(keep, target_is_directory=True)
    write_site(collect_pages(guide_paths(guides), include_members_only=False), out)
    assert not (out / "link").exists()
    assert (keep / "file.txt").read_text(encoding="utf-8") == "keep"


def test_write_site_refuses_a_folder_it_did_not_write(guides: Path, tmp_path: Path) -> None:
    out = tmp_path / "mine"
    out.mkdir()
    (out / "notes.txt").write_text("mine", encoding="utf-8")
    with pytest.raises(SiteError, match="is not empty and was not written by `site`"):
        write_site(collect_pages(guide_paths(guides), include_members_only=False), out)
    assert sorted(child.name for child in out.iterdir()) == ["notes.txt"]


def test_write_site_uses_an_empty_folder(guides: Path, tmp_path: Path) -> None:
    out = tmp_path / "empty"
    out.mkdir()
    write_site(collect_pages(guide_paths(guides), include_members_only=False), out)
    assert (out / SITE_MARKER).is_file()


@pytest.mark.parametrize("relative", [".", ".."])
def test_write_site_refuses_the_guides_folder_or_a_parent(guides: Path, relative: str) -> None:
    (guides / SITE_MARKER).write_text("", encoding="utf-8")  # even a marked folder is refused
    build = collect_pages(guide_paths(guides), include_members_only=False)
    with pytest.raises(SiteError, match="holds the guide "):
        write_site(build, guides / relative)
    assert (guides / "old.md").is_file()


def test_write_site_refuses_a_folder_holding_a_left_out_guide(tmp_path: Path) -> None:
    out = tmp_path / "site"
    (out / "drafts").mkdir(parents=True)
    (out / SITE_MARKER).write_text("", encoding="utf-8")
    guide = write_guide(out / "drafts", "members.md", "ccccccccccc", members_only=True)
    build = collect_pages([guide], include_members_only=False)
    assert build.left_out == [guide]
    with pytest.raises(SiteError, match="holds the guide "):
        write_site(build, out)
    assert guide.is_file()


def test_write_site_refuses_the_target_folder_of_a_symlinked_guide(guides: Path, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / SITE_MARKER).write_text("", encoding="utf-8")
    target = write_guide(elsewhere, "real.md", "fffffffffff", members_only=False)
    (guides / "link.md").symlink_to(target)
    with pytest.raises(SiteError, match="holds the guide "):
        write_site(collect_pages(guide_paths(guides), include_members_only=False), elsewhere)
    assert target.is_file()


def test_write_site_refuses_a_file(guides: Path, tmp_path: Path) -> None:
    out = tmp_path / "file"
    out.write_text("", encoding="utf-8")
    with pytest.raises(SiteError, match="is not a folder"):
        write_site(collect_pages(guide_paths(guides), include_members_only=False), out)
