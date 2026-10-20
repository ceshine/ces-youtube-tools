import datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ces_youtube_tools.cli import APP
from ces_youtube_tools.config import AppConfig, SiteConfig, GuideConfig, save_config
from ces_youtube_tools.static_site import (
    SitePage,
    SiteError,
    write_site,
    SITE_MARKER,
    render_index,
    collect_pages,
    is_publishable,
)
from ces_youtube_tools.front_matter import GuideMetadata, render_front_matter

RUNNER = CliRunner()
BODY = "# Watching Guide: The heading\n\n[0:05](https://www.youtube.com/watch?v={video_id}&t=5s): Hello.\n"


def write_guide(folder: Path, name: str, video_id: str, **fields: object) -> Path:
    """Write a guide with front matter; `fields` are `GuideMetadata` fields besides the video ID."""
    metadata = GuideMetadata.model_validate({"video_id": video_id, **fields})
    path = folder / name
    path.write_text(render_front_matter(metadata) + BODY.format(video_id=video_id), encoding="utf-8")
    return path


@pytest.fixture
def guides(tmp_path: Path) -> Path:
    folder = tmp_path / "guides"
    folder.mkdir()
    write_guide(folder, "old.md", "aaaaaaaaaaa", title="Old", upload_date="2026-10-01", members_only=False)
    write_guide(folder, "new.md", "bbbbbbbbbbb", title="New", upload_date="2026-10-08", members_only=False)
    write_guide(folder, "members.md", "ccccccccccc", title="Members", upload_date="2026-10-09", members_only=True)
    write_guide(folder, "unknown.md", "ddddddddddd", title="Unknown", members_only=None)
    (folder / "plain.md").write_text(BODY.format(video_id="eeeeeeeeeee"), encoding="utf-8")
    return folder


def guide_paths(folder: Path) -> list[Path]:
    return sorted(folder.glob("*.md"))


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
    assert '<a href="index.html">' in page.html
    assert "members-hint" not in page.html.split("<body>")[1]
    assert "video_id:" not in page.html


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


def test_render_index_escapes_and_marks_availability() -> None:
    def page(video_id: str, title: str, members_only: bool | None) -> SitePage:
        metadata = GuideMetadata(
            channel="A & B", upload_date=datetime.date(2026, 10, 8), duration=3725, members_only=members_only
        )
        return SitePage(source=Path("x.md"), video_id=video_id, title=title, metadata=metadata, html="")

    index = render_index([page("aaaaaaaaaaa", "<Title>", False), page("bbbbbbbbbbb", "Two", True)])
    assert "2 guides, newest upload first." in index
    assert '<a href="aaaaaaaaaaa.html">&lt;Title&gt;</a>' in index
    assert '<span class="channel">A &amp; B</span>' in index
    assert '<time datetime="2026-10-08">2026-10-08</time>' in index
    assert '<span class="length">1:02:05</span>' in index
    assert index.count('<span class="badge">Members only</span>') == 1
    assert "$" not in index

    unknown = render_index([page("ccccccccccc", "Three", None)])
    assert "1 guide, newest upload first." in unknown
    assert '<span class="badge">Availability unknown</span>' in unknown


def test_render_index_without_guides() -> None:
    assert '<p class="empty">No guides yet.</p>' in render_index([])


def test_write_site_writes_every_file(guides: Path, tmp_path: Path) -> None:
    out = tmp_path / "site" / "nested"
    write_site(collect_pages(guide_paths(guides), include_members_only=False), out)
    assert sorted(child.name for child in out.iterdir()) == [
        SITE_MARKER,
        "_headers",
        "aaaaaaaaaaa.html",
        "bbbbbbbbbbb.html",
        "index.html",
        "robots.txt",
    ]
    assert (out / "robots.txt").read_text(encoding="utf-8") == "User-agent: *\nDisallow: /\n"
    headers = (out / "_headers").read_text(encoding="utf-8")
    assert headers.startswith("/*\n") and "  X-Robots-Tag: noindex, nofollow\n" in headers
    assert "no-referrer" not in headers  # the player needs the Referer


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


@pytest.fixture
def config_path(guides: Path, tmp_path: Path) -> Path:
    path = tmp_path / "config" / "config.toml"
    config = AppConfig(guide=GuideConfig(output_dir=guides), site=SiteConfig(output_dir=tmp_path / "configured-site"))
    save_config(config, path)
    return path


def test_site_command_uses_the_configured_folders(config_path: Path, tmp_path: Path) -> None:
    out = tmp_path / "configured-site"
    result = RUNNER.invoke(APP, ["site", "--config", str(config_path)], env={"COLUMNS": "300"})
    assert result.exit_code == 0, result.output
    assert f"Wrote 2 guide pages and the index to {out}." in result.output
    assert "Left out 3 members-only guides or guides whose availability is unknown" in result.output
    assert f"npx wrangler pages deploy {out}" in result.output
    assert "Cloudflare Access" not in result.output
    assert (out / "index.html").is_file()


def test_site_command_out_option_beats_config(config_path: Path, tmp_path: Path) -> None:
    out = tmp_path / "from-option"
    result = RUNNER.invoke(APP, ["site", "-o", str(out), "--config", str(config_path)])
    assert result.exit_code == 0, result.output
    assert (out / "index.html").is_file()
    assert not (tmp_path / "configured-site").exists()


def test_site_command_warns_when_members_only_guides_are_included(
    config_path: Path, guides: Path, tmp_path: Path
) -> None:
    out = tmp_path / "site"
    args = ["site", str(guides), "-o", str(out), "--include-members-only", "--config", str(config_path)]
    result = RUNNER.invoke(APP, args, env={"COLUMNS": "300"})
    assert result.exit_code == 0, result.output
    assert "Wrote 5 guide pages" in result.output
    assert "Deploy this build only to a project behind Cloudflare Access." in result.output
    assert "Left out" not in result.output


def test_site_command_fails_without_writing(config_path: Path, guides: Path, tmp_path: Path) -> None:
    (guides / "nolinks.md").write_text("---\nmembers_only: false\n---\n# No links\n", encoding="utf-8")
    out = tmp_path / "site"
    result = RUNNER.invoke(APP, ["site", "-o", str(out), "--config", str(config_path)], env={"COLUMNS": "300"})
    assert result.exit_code == 1
    assert "1 problem found, so the site was not built" in result.output
    assert not out.exists()


def test_site_command_needs_a_folder(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    save_config(AppConfig(), config_path)
    result = RUNNER.invoke(APP, ["site", "-o", str(tmp_path / "site"), "--config", str(config_path)])
    assert result.exit_code == 1
    assert "guide.output_dir is not set" in result.output


def test_site_command_expands_home_before_protecting_the_guides(
    config_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    (home / "site" / "guides").mkdir(parents=True)
    (home / "site" / SITE_MARKER).write_text("", encoding="utf-8")
    guide = write_guide(home / "site" / "guides", "keep.md", "aaaaaaaaaaa", members_only=False)
    monkeypatch.setenv("HOME", str(home))
    args = ["site", "~/site/guides", "-o", "~/site", "--config", str(config_path)]
    result = RUNNER.invoke(APP, args, env={"COLUMNS": "300"})
    assert result.exit_code == 1
    assert "holds the guide" in result.output
    assert guide.is_file()


def test_site_command_expands_home_in_the_output_folder(
    config_path: Path, guides: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    result = RUNNER.invoke(APP, ["site", str(guides), "--out=~/out", "--config", str(config_path)])
    assert result.exit_code == 0, result.output
    assert (home / "out" / "index.html").is_file()
    assert not (tmp_path / "~").exists()


def test_site_command_needs_an_output_folder(guides: Path, tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    save_config(AppConfig(), config_path)
    result = RUNNER.invoke(APP, ["site", str(guides), "--config", str(config_path)])
    assert result.exit_code == 1
    assert "site.output_dir is not set" in result.output
