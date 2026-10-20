from pathlib import Path

import pytest

from ces_youtube_tools.guide_html import expand_paths, GuideHtmlError

FIXTURE = Path(__file__).parent / "fixtures" / "sample_guide.md"


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
