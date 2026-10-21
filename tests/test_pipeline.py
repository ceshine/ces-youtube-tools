import json
import logging
import datetime
import subprocess
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from ces_youtube_tools.guide import GuideError
from ces_youtube_tools.video import VideoInfo
from ces_youtube_tools.ytdlp import YtDlp
from ces_youtube_tools.config import AppConfig
from ces_youtube_tools.pipeline import (
    CacheError,
    write_guide,
    work_dir_for,
    load_transcript,
    load_video_info,
    load_prompt_template,
    make_guide_generator,
    find_guides_for_video,
    make_transcript_provider,
)
from ces_youtube_tools.subtitles import YtDlpSubtitleProvider
from ces_youtube_tools.claude_cli import ClaudeCliGenerator
from ces_youtube_tools.transcript import Transcript, TranscriptSegment
from ces_youtube_tools.front_matter import split_front_matter, metadata_from_video, render_front_matter

VIDEO_JSON = {"id": "abcdefghijk", "title": "A video", "formats": [{"url": "https://signed.example/x"}]}


class CountingRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        return subprocess.CompletedProcess(args, 0, json.dumps(VIDEO_JSON), "")


class FakeProvider:
    def __init__(self) -> None:
        self.calls = 0

    def fetch(self, video: VideoInfo, work_dir: Path) -> Transcript:
        self.calls += 1
        return Transcript(
            video_id=video.id, source="fake", language="en", segments=[TranscriptSegment(start=0, end=1, text="hi")]
        )


def test_work_dir_for(tmp_path: Path) -> None:
    work_dir = work_dir_for(tmp_path / "cache", "abcdefghijk")
    assert work_dir == tmp_path / "cache" / "abcdefghijk"
    assert work_dir.is_dir()


def test_load_video_info_caches_only_the_needed_fields(tmp_path: Path) -> None:
    runner = CountingRunner()
    ytdlp = YtDlp(["yt-dlp"], "firefox:/p", runner)
    first = load_video_info("abcdefghijk", ytdlp, tmp_path)
    second = load_video_info("abcdefghijk", ytdlp, tmp_path)
    assert first == second
    assert first.title == "A video"
    assert len(runner.calls) == 1
    assert runner.calls[0][-2:] == ["--skip-download", "https://www.youtube.com/watch?v=abcdefghijk"]
    assert "signed.example" not in (tmp_path / "video.json").read_text(encoding="utf-8")


def test_load_video_info_refresh(tmp_path: Path) -> None:
    runner = CountingRunner()
    ytdlp = YtDlp(["yt-dlp"], None, runner)
    _ = load_video_info("abcdefghijk", ytdlp, tmp_path)
    _ = load_video_info("abcdefghijk", ytdlp, tmp_path, refresh=True)
    assert len(runner.calls) == 2


def test_invalid_cache_is_rebuilt(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    (tmp_path / "video.json").write_text('{"not": "a video"}', encoding="utf-8")
    runner = CountingRunner()
    with caplog.at_level(logging.WARNING):
        info = load_video_info("abcdefghijk", YtDlp(["yt-dlp"], None, runner), tmp_path)
    assert info.title == "A video"
    assert len(runner.calls) == 1
    assert "Ignoring invalid cache file" in caplog.text


def test_video_cache_without_the_publish_time_keys_is_fetched_again(tmp_path: Path) -> None:
    (tmp_path / "video.json").write_text('{"id": "abcdefghijk", "title": "Old"}', encoding="utf-8")
    runner = CountingRunner()
    info = load_video_info("abcdefghijk", YtDlp(["yt-dlp"], None, runner), tmp_path)
    assert info.title == "A video"
    assert len(runner.calls) == 1
    cached = json.loads((tmp_path / "video.json").read_text(encoding="utf-8"))
    assert (cached["timestamp"], cached["release_timestamp"]) == (None, None)


def test_video_cache_with_null_publish_times_is_used(tmp_path: Path) -> None:
    text = '{"id": "abcdefghijk", "title": "Cached", "timestamp": null, "release_timestamp": null}'
    (tmp_path / "video.json").write_text(text, encoding="utf-8")
    runner = CountingRunner()
    info = load_video_info("abcdefghijk", YtDlp(["yt-dlp"], None, runner), tmp_path)
    assert info.title == "Cached"
    assert runner.calls == []


def test_work_dir_for_reports_an_unusable_cache_dir(tmp_path: Path) -> None:
    (tmp_path / "cache").write_text("not a directory", encoding="utf-8")
    with pytest.raises(CacheError, match="Cannot create the work directory .*Check cache_dir"):
        _ = work_dir_for(tmp_path / "cache", "abcdefghijk")


def test_cache_with_invalid_utf8_is_rebuilt(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    (tmp_path / "video.json").write_bytes(b"\xff\xfe not utf-8")
    runner = CountingRunner()
    with caplog.at_level(logging.WARNING):
        info = load_video_info("abcdefghijk", YtDlp(["yt-dlp"], None, runner), tmp_path)
    assert info.title == "A video"
    assert "Ignoring invalid cache file" in caplog.text


def test_load_video_info_reports_a_failed_cache_write(tmp_path: Path) -> None:
    # A directory in place of the cache file can be neither read (so it is ignored) nor written.
    (tmp_path / "video.json").mkdir()
    runner = CountingRunner()
    with pytest.raises(CacheError, match=r"Cannot write the cache file .*video\.json"):
        _ = load_video_info("abcdefghijk", YtDlp(["yt-dlp"], None, runner), tmp_path)
    assert len(runner.calls) == 1


def test_load_transcript_reports_a_failed_cache_write(tmp_path: Path) -> None:
    (tmp_path / "transcript.json").mkdir()
    with pytest.raises(CacheError, match=r"Cannot write the cache file .*transcript\.json"):
        _ = load_transcript(VideoInfo(id="abcdefghijk", title="A video"), FakeProvider(), tmp_path)


def test_load_transcript_caches(tmp_path: Path) -> None:
    provider = FakeProvider()
    video = VideoInfo(id="abcdefghijk", title="A video")
    first = load_transcript(video, provider, tmp_path)
    second = load_transcript(video, provider, tmp_path)
    assert first == second
    assert provider.calls == 1
    _ = load_transcript(video, provider, tmp_path, refresh=True)
    assert provider.calls == 2


def test_make_transcript_provider() -> None:
    config = AppConfig()
    config.transcript.languages = ["de"]
    provider = make_transcript_provider(config, YtDlp(["yt-dlp"], None))
    assert isinstance(provider, YtDlpSubtitleProvider)
    assert provider.languages == ["de"]


def test_make_transcript_provider_unknown() -> None:
    config = AppConfig()
    config.transcript.provider = "whisper"
    with pytest.raises(ValueError, match="Unknown transcript provider 'whisper'. Available: yt-dlp-subs"):
        _ = make_transcript_provider(config, YtDlp(["yt-dlp"], None))


class FakeGenerator:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    def generate(self, prompt: str, video: VideoInfo, work_dir: Path) -> str:
        self.prompts.append(prompt)
        return self.reply


NOW = datetime.datetime(2026, 10, 9, 10, 30, 15, 123456, tzinfo=datetime.timezone(datetime.timedelta(hours=8)))
GUIDE_VIDEO = VideoInfo(id="abcdefghijk", title="A video: part 1", duration=600, upload_date="20261008")
GUIDE_TRANSCRIPT = Transcript(
    video_id="abcdefghijk", source="fake", language="en", segments=[TranscriptSegment(start=0, end=1, text="hi")]
)


def guide_body(path: Path) -> str:
    return split_front_matter(path.read_text(encoding="utf-8"))[1]


REPLY = "```markdown\n# Guide\n\n[1:10](https://www.youtube.com/watch?v=abcdefghijk&t=67s)\n```"


def test_write_guide(tmp_path: Path) -> None:
    generator = FakeGenerator(REPLY)
    work_dir, output_dir = tmp_path / "work", tmp_path / "out" / "nested"
    work_dir.mkdir()
    video = GUIDE_VIDEO.model_copy(update={"timestamp": 1791477000})  # 2026-10-08T16:30:00Z
    template = "Guide for $title, $upload_date, $today"
    result = write_guide(
        video, GUIDE_TRANSCRIPT, template, generator, work_dir, output_dir, NOW, timezone=ZoneInfo("Asia/Taipei")
    )
    # The file name and `$upload_date` take the publish day in Asia/Taipei; `upload_date` stays as yt-dlp reports it.
    assert result.path == output_dir / "Watching Guide - 20261009 - A video part 1.md"
    assert result.path.read_text(encoding="utf-8") == (
        "---\n"
        "video_id: abcdefghijk\n"
        "title: 'A video: part 1'\n"
        "channel: null\n"
        "channel_id: null\n"
        "upload_date: 2026-10-08\n"
        "published: '2026-10-08T16:30:00Z'\n"
        "duration: 600\n"
        "members_only: null\n"
        "generated: '2026-10-09T02:30:15Z'\n"
        "---\n"
        "\n"
        "# Guide\n\n[1:07](https://www.youtube.com/watch?v=abcdefghijk&t=67s)\n"
    )
    assert (result.links.links, result.links.relabeled) == (1, 1)
    assert result.removed == []
    assert generator.prompts == ["Guide for A video: part 1, Oct 9, 2026, Oct 9, 2026"]
    assert (work_dir / "prompt.md").read_text(encoding="utf-8") == generator.prompts[0]
    assert (work_dir / "guide.md").read_text(encoding="utf-8") == REPLY


def test_write_guide_refuses_to_overwrite_before_generating(tmp_path: Path) -> None:
    existing = tmp_path / "Watching Guide - 20261008 - A video part 1.md"
    existing.write_text("mine", encoding="utf-8")
    generator = FakeGenerator(REPLY)
    with pytest.raises(GuideError, match="already exists"):
        _ = write_guide(GUIDE_VIDEO, GUIDE_TRANSCRIPT, "$title", generator, tmp_path, tmp_path, NOW)
    assert generator.prompts == []
    assert existing.read_text(encoding="utf-8") == "mine"
    _ = write_guide(GUIDE_VIDEO, GUIDE_TRANSCRIPT, "$title", generator, tmp_path, tmp_path, NOW, force=True)
    assert guide_body(existing).startswith("# Guide")


def old_guide(path: Path, video: VideoInfo = GUIDE_VIDEO) -> Path:
    path.write_text(render_front_matter(metadata_from_video(video, None)) + "# Old guide\n", encoding="utf-8")
    return path


def test_write_guide_refuses_another_guide_for_the_video_before_generating(tmp_path: Path) -> None:
    old = old_guide(tmp_path / "Watching Guide - 20261007 - Old name.md")
    generator = FakeGenerator(REPLY)
    scan = find_guides_for_video(tmp_path, GUIDE_VIDEO.id)
    with pytest.raises(GuideError, match=r"Video abcdefghijk already has a guide: .*Old name\.md\. Use --force"):
        _ = write_guide(GUIDE_VIDEO, GUIDE_TRANSCRIPT, "$title", generator, tmp_path, tmp_path, NOW, existing=scan)
    assert generator.prompts == []
    assert guide_body(old) == "# Old guide\n"

    result = write_guide(
        GUIDE_VIDEO, GUIDE_TRANSCRIPT, "$title", generator, tmp_path, tmp_path, NOW, existing=scan, force=True
    )
    assert result.path == tmp_path / "Watching Guide - 20261008 - A video part 1.md"
    assert result.removed == [old]
    assert not old.exists()


def test_write_guide_rejects_a_scan_for_another_video(tmp_path: Path) -> None:
    old = old_guide(tmp_path / "Other video.md", GUIDE_VIDEO.model_copy(update={"id": "zzzzzzzzzzz"}))
    scan = find_guides_for_video(tmp_path, "zzzzzzzzzzz")
    generator = FakeGenerator(REPLY)
    with pytest.raises(ValueError, match="scan is for video zzzzzzzzzzz, not abcdefghijk"):
        _ = write_guide(
            GUIDE_VIDEO, GUIDE_TRANSCRIPT, "$title", generator, tmp_path, tmp_path, NOW, existing=scan, force=True
        )
    assert generator.prompts == []
    assert old.exists()


def test_write_guide_never_replaces_a_file_found_only_by_its_links(tmp_path: Path) -> None:
    notes = tmp_path / "My notes.md"
    notes.write_text(REPLY, encoding="utf-8")
    scan = find_guides_for_video(tmp_path, GUIDE_VIDEO.id)
    generator = FakeGenerator(REPLY)
    with pytest.raises(GuideError, match=r"My notes\.md links to this video, but .* not replaced, even with --force"):
        _ = write_guide(
            GUIDE_VIDEO, GUIDE_TRANSCRIPT, "$title", generator, tmp_path, tmp_path, NOW, existing=scan, force=True
        )
    assert generator.prompts == []
    assert notes.read_text(encoding="utf-8") == REPLY


def test_write_guide_overwrites_a_guide_with_the_same_name_without_removing_it(tmp_path: Path) -> None:
    existing = old_guide(tmp_path / "Watching Guide - 20261008 - A video part 1.md")
    scan = find_guides_for_video(tmp_path, GUIDE_VIDEO.id)
    generator = FakeGenerator(REPLY)
    result = write_guide(
        GUIDE_VIDEO, GUIDE_TRANSCRIPT, "$title", generator, tmp_path, tmp_path, NOW, existing=scan, force=True
    )
    assert result.removed == []
    assert guide_body(existing).startswith("# Guide")


def test_write_guide_keeps_another_name_of_the_new_guide(tmp_path: Path) -> None:
    # On a case-insensitive file system, or through a link, an "older" guide can be the file just written.
    target = old_guide(tmp_path / "Watching Guide - 20261008 - A video part 1.md")
    alias = tmp_path / "Watching Guide - 20261007 - Old name.md"
    alias.symlink_to(target.name)
    scan = find_guides_for_video(tmp_path, GUIDE_VIDEO.id)
    assert scan.paths == [alias, target]
    generator = FakeGenerator(REPLY)
    result = write_guide(
        GUIDE_VIDEO, GUIDE_TRANSCRIPT, "$title", generator, tmp_path, tmp_path, NOW, existing=scan, force=True
    )
    assert result.removed == []
    assert alias.is_symlink()
    assert guide_body(target).startswith("# Guide")


def test_write_guide_says_when_an_older_guide_cannot_be_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = old_guide(tmp_path / "Watching Guide - 20261007 - Old name.md")
    scan = find_guides_for_video(tmp_path, GUIDE_VIDEO.id)

    def refuse(self: Path, missing_ok: bool = False) -> None:
        raise PermissionError("not allowed")

    monkeypatch.setattr(Path, "unlink", refuse)
    generator = FakeGenerator(REPLY)
    with pytest.raises(GuideError, match=r"Wrote .*A video part 1\.md, but cannot remove the older guide .*Old name"):
        _ = write_guide(
            GUIDE_VIDEO, GUIDE_TRANSCRIPT, "$title", generator, tmp_path, tmp_path, NOW, existing=scan, force=True
        )
    assert old.exists()
    assert guide_body(tmp_path / "Watching Guide - 20261008 - A video part 1.md").startswith("# Guide")


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


def test_write_guide_checks_the_output_directory_before_generating(tmp_path: Path) -> None:
    (tmp_path / "a-file").write_text("not a directory", encoding="utf-8")
    generator = FakeGenerator(REPLY)
    with pytest.raises(GuideError, match="Cannot create the output directory"):
        _ = write_guide(GUIDE_VIDEO, GUIDE_TRANSCRIPT, "$title", generator, tmp_path, tmp_path / "a-file" / "out", NOW)
    assert generator.prompts == []


def test_write_guide_reports_a_failed_write_and_keeps_the_raw_reply(tmp_path: Path) -> None:
    work_dir, output_dir = tmp_path / "work", tmp_path / "out"
    work_dir.mkdir()
    # A directory where the guide should go makes the write fail after generating.
    (output_dir / "Watching Guide - 20261008 - A video part 1.md").mkdir(parents=True)
    generator = FakeGenerator(REPLY)
    with pytest.raises(GuideError, match="Cannot write .*raw reply is kept in"):
        _ = write_guide(GUIDE_VIDEO, GUIDE_TRANSCRIPT, "$title", generator, work_dir, output_dir, NOW, force=True)
    assert (work_dir / "guide.md").read_text(encoding="utf-8") == REPLY


def test_write_guide_reports_an_unsaved_prompt_before_generating(tmp_path: Path) -> None:
    work_dir = tmp_path / "work"
    (work_dir / "prompt.md").mkdir(parents=True)
    generator = FakeGenerator(REPLY)
    with pytest.raises(CacheError, match=r"Cannot write the cache file .*prompt\.md"):
        _ = write_guide(GUIDE_VIDEO, GUIDE_TRANSCRIPT, "$title", generator, work_dir, tmp_path / "out", NOW)
    assert generator.prompts == []


def test_write_guide_still_writes_the_guide_when_the_raw_reply_is_not_saved(tmp_path: Path) -> None:
    work_dir = tmp_path / "work"
    (work_dir / "guide.md").mkdir(parents=True)
    result = write_guide(GUIDE_VIDEO, GUIDE_TRANSCRIPT, "$title", FakeGenerator(REPLY), work_dir, tmp_path, NOW)
    assert guide_body(result.path).startswith("# Guide")
    assert result.raw_reply_error is not None
    assert "Cannot write the cache file" in result.raw_reply_error


def test_write_guide_says_when_the_generation_is_lost(tmp_path: Path) -> None:
    work_dir, output_dir = tmp_path / "work", tmp_path / "out"
    (work_dir / "guide.md").mkdir(parents=True)
    (output_dir / "Watching Guide - 20261008 - A video part 1.md").mkdir(parents=True)
    generator = FakeGenerator(REPLY)
    with pytest.raises(GuideError, match="could not be saved either, so the generation is lost"):
        _ = write_guide(GUIDE_VIDEO, GUIDE_TRANSCRIPT, "$title", generator, work_dir, output_dir, NOW, force=True)


def test_write_guide_rejects_bad_template_before_generating(tmp_path: Path) -> None:
    generator = FakeGenerator(REPLY)
    with pytest.raises(GuideError, match="Unknown placeholder"):
        _ = write_guide(GUIDE_VIDEO, GUIDE_TRANSCRIPT, "$nope", generator, tmp_path, tmp_path, NOW)
    assert generator.prompts == []


def test_load_prompt_template(tmp_path: Path) -> None:
    text, is_default = load_prompt_template(tmp_path / "missing.md")
    assert is_default and "$transcript" in text
    custom = tmp_path / "custom.md"
    custom.write_text("Mine: $title", encoding="utf-8")
    assert load_prompt_template(custom) == ("Mine: $title", False)


def test_make_guide_generator() -> None:
    config = AppConfig()
    config.guide.options["claude-cli"] = {"model": "opus"}
    generator = make_guide_generator(config)
    assert isinstance(generator, ClaudeCliGenerator)
    assert generator.options.model == "opus"


def test_make_guide_generator_errors() -> None:
    config = AppConfig()
    config.guide.generator = "openai"
    with pytest.raises(ValueError, match="Unknown guide generator 'openai'. Available: claude-cli"):
        _ = make_guide_generator(config)
    config = AppConfig()
    config.guide.options["claude-cli"] = {"modle": "opus"}
    with pytest.raises(ValueError, match=r"Invalid settings in \[guide.options.claude-cli\]"):
        _ = make_guide_generator(config)
