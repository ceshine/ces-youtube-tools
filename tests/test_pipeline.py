import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from contextlib import nullcontext, AbstractContextManager

import pytest

from ces_youtube_tools.cache import CacheError, work_dir_for
from ces_youtube_tools.guide import GuideError
from ces_youtube_tools.video import VideoInfo
from ces_youtube_tools.ytdlp import YtDlp
from ces_youtube_tools.config import AppConfig
from ces_youtube_tools.pipeline import (
    GuideTools,
    write_guide,
    prepare_guide,
    generate_guide,
    make_guide_tools,
    load_prompt_template,
    make_transcript_provider,
)
from ces_youtube_tools.subtitles import YtDlpSubtitleProvider
from ces_youtube_tools.generators.claude_cli import ClaudeCliGenerator
from ces_youtube_tools.transcript import Transcript, TranscriptSegment
from ces_youtube_tools.front_matter import split_front_matter, metadata_from_video, render_front_matter
from ces_youtube_tools.existing_guides import find_guides_for_video


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
        self.harness: str = "Fake harness"
        self.model: str = "fake-model"
        self.effort: str | None = "high"

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
        "harness: Fake harness\n"
        "model: fake-model\n"
        "effort: high\n"
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


def test_make_guide_tools(tmp_path: Path) -> None:
    config = AppConfig()
    config.cache_dir = tmp_path / "cache"
    config.guide.prompt_template = tmp_path / "missing.md"
    tools = make_guide_tools(config)
    assert isinstance(tools.provider, YtDlpSubtitleProvider)
    assert isinstance(tools.generator, ClaudeCliGenerator)
    assert tools.generator_name == "claude-cli"
    assert tools.template_is_default
    assert tools.cache_dir == tmp_path / "cache"

    _ = config.guide.prompt_template.write_text("Mine: $title", encoding="utf-8")
    tools = make_guide_tools(config)
    assert (tools.template, tools.template_is_default) == ("Mine: $title", False)


class FakeProvider:
    def __init__(self) -> None:
        self.fetched: list[str] = []

    def fetch(self, video: VideoInfo, work_dir: Path) -> Transcript:
        self.fetched.append(video.id)
        return GUIDE_TRANSCRIPT


class StatusRecorder:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def __call__(self, message: str) -> AbstractContextManager[object]:
        self.messages.append(message)
        return nullcontext()


def guide_tools(tmp_path: Path, provider: FakeProvider, generator: FakeGenerator) -> GuideTools:
    """Tools whose cache already holds `GUIDE_VIDEO`'s metadata, so yt-dlp is never run."""
    cache_dir = tmp_path / "cache"
    work_dir = work_dir_for(cache_dir, GUIDE_VIDEO.id)
    _ = (work_dir / "video.json").write_text(GUIDE_VIDEO.model_dump_json(), encoding="utf-8")
    return GuideTools(
        ytdlp=YtDlp(["yt-dlp-must-not-run"], None),
        provider=provider,
        generator=generator,
        generator_name="fake",
        template="Guide for $title",
        template_is_default=False,
        timezone=None,
        cache_dir=cache_dir,
    )


def test_prepare_guide(tmp_path: Path) -> None:
    tools = guide_tools(tmp_path, FakeProvider(), FakeGenerator(REPLY))
    out = tmp_path / "out"
    out.mkdir()
    old = old_guide(out / "Watching Guide - 20261007 - Old name.md")
    prepared = prepare_guide(tools, GUIDE_VIDEO.id, out, refresh=False)
    assert prepared.info == GUIDE_VIDEO
    assert prepared.work_dir == work_dir_for(tools.cache_dir, GUIDE_VIDEO.id)
    assert prepared.target == out / "Watching Guide - 20261008 - A video part 1.md"
    assert prepared.scan.video_id == GUIDE_VIDEO.id
    assert prepared.scan.paths == [old]


def test_generate_guide(tmp_path: Path) -> None:
    provider, generator, status = FakeProvider(), FakeGenerator(REPLY), StatusRecorder()
    tools = guide_tools(tmp_path, provider, generator)
    prepared = prepare_guide(tools, GUIDE_VIDEO.id, tmp_path / "out", refresh=False, status=status)
    result = generate_guide(tools, prepared, force=False, refresh=False, status=status)
    assert result.path == prepared.target
    assert guide_body(result.path).startswith("# Guide")
    assert generator.prompts == ["Guide for A video: part 1"]
    assert provider.fetched == [GUIDE_VIDEO.id]
    assert (prepared.work_dir / "transcript.json").exists()
    assert status.messages == [
        "Fetching video metadata...",
        "Fetching transcript...",
        "Writing the guide with fake (this can take a few minutes)...",
    ]


def test_generate_guide_refuses_an_existing_guide_before_fetching_the_transcript(tmp_path: Path) -> None:
    provider, generator = FakeProvider(), FakeGenerator(REPLY)
    tools = guide_tools(tmp_path, provider, generator)
    out = tmp_path / "out"
    out.mkdir()
    _ = old_guide(out / "Watching Guide - 20261007 - Old name.md")
    prepared = prepare_guide(tools, GUIDE_VIDEO.id, out, refresh=False)
    with pytest.raises(GuideError, match="already has a guide"):
        _ = generate_guide(tools, prepared, force=False, refresh=False)
    assert provider.fetched == []
    assert generator.prompts == []

    result = generate_guide(tools, prepared, force=True, refresh=False)
    assert result.removed == prepared.scan.paths
