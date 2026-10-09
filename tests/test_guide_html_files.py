from pathlib import Path

import pytest
from typer.testing import CliRunner

from youtube_tools.cli import APP
from youtube_tools.guide_html import expand_paths, GuideHtmlError, PageExistsError, write_guide_page

FIXTURE = Path(__file__).parent / "fixtures" / "sample_guide.md"
RUNNER = CliRunner()


@pytest.fixture
def guides(tmp_path: Path) -> Path:
    """A folder with guides, a guide in a subfolder, a guide without links and a non-Markdown file."""
    folder = tmp_path / "guides"
    (folder / "sub").mkdir(parents=True)
    text = FIXTURE.read_text(encoding="utf-8")
    (folder / "a.md").write_text(text, encoding="utf-8")
    (folder / "b.md").write_text(text, encoding="utf-8")
    (folder / "sub" / "c.md").write_text(text, encoding="utf-8")
    (folder / "nolinks.md").write_text("# No links\n", encoding="utf-8")
    (folder / "notes.txt").write_text("text", encoding="utf-8")
    return folder


def names(paths: list[Path]) -> list[str]:
    return [path.name for path in paths]


def test_write_guide_page_next_to_the_guide(guides: Path) -> None:
    target = write_guide_page(guides / "a.md")
    assert target == guides / "a.html"
    assert "abcdefghijk" in target.read_text(encoding="utf-8")


def test_write_guide_page_keeps_existing_page(guides: Path) -> None:
    (guides / "a.html").write_text("old", encoding="utf-8")
    with pytest.raises(PageExistsError, match="a.html"):
        write_guide_page(guides / "a.md")
    assert (guides / "a.html").read_text(encoding="utf-8") == "old"


def test_write_guide_page_overwrites_when_asked(guides: Path) -> None:
    (guides / "a.html").write_text("old", encoding="utf-8")
    assert write_guide_page(guides / "a.md", overwrite=True).read_text(encoding="utf-8") != "old"


def test_write_guide_page_explicit_id_beats_links(guides: Path) -> None:
    page = write_guide_page(guides / "a.md", video_id="AAAAAAAAAAA").read_text(encoding="utf-8")
    assert 'var VIDEO_ID = "AAAAAAAAAAA";' in page


def test_write_guide_page_without_video_id_names_the_file(guides: Path) -> None:
    with pytest.raises(GuideHtmlError, match="nolinks.md"):
        write_guide_page(guides / "nolinks.md")
    assert not (guides / "nolinks.html").exists()


def test_expand_paths_single_file(guides: Path) -> None:
    assert expand_paths(["a.md"], guides) == [guides / "a.md"]


def test_expand_paths_folder_is_not_recursive(guides: Path) -> None:
    assert names(expand_paths(["."], guides)) == ["a.md", "b.md", "nolinks.md"]


def test_expand_paths_ignores_other_files_and_duplicates(guides: Path, tmp_path: Path) -> None:
    result = expand_paths([str(guides), "guides/a.md", "guides/notes.txt", "guides/sub"], tmp_path)
    assert names(result) == ["a.md", "b.md", "nolinks.md", "c.md"]


def test_expand_paths_missing_path(guides: Path) -> None:
    with pytest.raises(GuideHtmlError, match="missing.md"):
        expand_paths(["missing.md"], guides)


def test_expand_paths_folder_without_markdown(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    with pytest.raises(GuideHtmlError, match="empty"):
        expand_paths(["empty"], tmp_path)


def test_expand_paths_only_non_markdown_files(guides: Path) -> None:
    with pytest.raises(GuideHtmlError, match="No Markdown files"):
        expand_paths(["notes.txt"], guides)


def test_expand_paths_unreadable_folder(guides: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def deny(self: Path):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "iterdir", deny)
    with pytest.raises(GuideHtmlError, match=r"Cannot read 'sub'.*denied"):
        expand_paths(["sub"], guides)


def test_html_command_converts_a_folder_and_reports_guides_without_video(
    guides: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(guides)
    result = RUNNER.invoke(APP, ["html", ".", "notes.txt"])
    assert result.exit_code == 1
    assert (guides / "a.html").exists() and (guides / "b.html").exists()
    assert not (guides / "nolinks.html").exists()
    assert not (guides / "sub" / "c.html").exists()
    assert "2 written, 1 skipped." in result.output
    assert "notes.txt" not in result.output


def test_html_command_fails_when_no_markdown_file_is_left(guides: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(guides)
    result = RUNNER.invoke(APP, ["html", "notes.txt"])
    assert result.exit_code == 1
    assert "No Markdown files" in result.output


def test_html_command_all_good(guides: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(guides)
    result = RUNNER.invoke(APP, ["html", "a.md", "sub"])
    assert result.exit_code == 0
    assert (guides / "a.html").exists() and (guides / "sub" / "c.html").exists()
    assert "2 written, 0 skipped." in result.output


def test_html_command_missing_path(guides: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(guides)
    result = RUNNER.invoke(APP, ["html", "missing.md"])
    assert result.exit_code == 1
    assert "missing.md" in result.output
    assert not (guides / "a.html").exists()


def test_html_command_skips_existing_pages_with_a_warning(guides: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(guides)
    (guides / "a.html").write_text("old", encoding="utf-8")
    result = RUNNER.invoke(APP, ["html", "a.md", "b.md"])
    assert result.exit_code == 0
    assert (guides / "a.html").read_text(encoding="utf-8") == "old"
    assert (guides / "b.html").exists()
    assert "a.html already exists" in result.output and "--overwrite" in result.output
    assert "1 written, 1 skipped." in result.output


def test_html_command_overwrite_flag(guides: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(guides)
    (guides / "a.html").write_text("old", encoding="utf-8")
    result = RUNNER.invoke(APP, ["html", "a.md", "--overwrite"])
    assert result.exit_code == 0
    assert (guides / "a.html").read_text(encoding="utf-8") != "old"
    assert "1 written, 0 skipped." in result.output
