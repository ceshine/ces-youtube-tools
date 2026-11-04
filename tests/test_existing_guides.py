from pathlib import Path

from ces_youtube_tools.video import VideoInfo
from ces_youtube_tools.front_matter import metadata_from_video, render_front_matter
from ces_youtube_tools.existing_guides import find_guides_for_video

GUIDE_VIDEO = VideoInfo(id="abcdefghijk", title="A video: part 1", duration=600, upload_date="20261008")
REPLY = "```markdown\n# Guide\n\n[1:10](https://www.youtube.com/watch?v=abcdefghijk&t=67s)\n```"


def old_guide(path: Path, video: VideoInfo = GUIDE_VIDEO) -> Path:
    path.write_text(render_front_matter(metadata_from_video(video, None)) + "# Old guide\n", encoding="utf-8")
    return path


def test_find_guides_for_video(tmp_path: Path) -> None:
    old_guide(tmp_path / "b with front matter.md")
    (tmp_path / "a without front matter.MD").write_text(REPLY, encoding="utf-8")
    (tmp_path / "c without video_id.md").write_text("---\ntitle: x\n---\n" + REPLY, encoding="utf-8")
    other = GUIDE_VIDEO.model_copy(update={"id": "zzzzzzzzzzz"})
    # The front matter names another video, so its links do not count.
    (tmp_path / "other video.md").write_text(
        render_front_matter(metadata_from_video(other, None)) + REPLY, encoding="utf-8"
    )
    (tmp_path / "not markdown.txt").write_text(REPLY, encoding="utf-8")
    (tmp_path / "folder.md").mkdir()
    scan = find_guides_for_video(tmp_path, "abcdefghijk")
    assert scan.paths == [tmp_path / "b with front matter.md"]
    assert scan.linked == [tmp_path / "a without front matter.MD", tmp_path / "c without video_id.md"]
    assert scan.warnings == []


def test_find_guides_for_video_reports_files_it_cannot_check(tmp_path: Path) -> None:
    (tmp_path / "bad front matter.md").write_text("---\nmembers_only: maybe\n---\n" + REPLY, encoding="utf-8")
    (tmp_path / "not utf-8.md").write_bytes(b"\xff\xfe")
    scan = find_guides_for_video(tmp_path, "abcdefghijk")
    assert (scan.paths, scan.linked) == ([], [tmp_path / "bad front matter.md"])  # judged by its links
    assert len(scan.warnings) == 2
    assert "invalid front matter" in scan.warnings[0] and "Its links were used" in scan.warnings[0]
    assert scan.warnings[1].startswith(f"Cannot read {tmp_path / 'not utf-8.md'}")


def test_find_guides_for_video_in_a_missing_folder(tmp_path: Path) -> None:
    scan = find_guides_for_video(tmp_path / "missing", "abcdefghijk")
    assert (scan.paths, scan.linked, scan.warnings) == ([], [], [])
