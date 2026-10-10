import re
import datetime

import pytest

from ces_youtube_tools.guide import (
    GuideError,
    clean_output,
    render_prompt,
    group_segments,
    guide_filename,
    default_template,
    format_upload_date,
    format_seconds_label,
    TEMPLATE_PLACEHOLDERS,
    check_timestamp_links,
)
from ces_youtube_tools.video import Chapter, VideoInfo
from ces_youtube_tools.transcript import Transcript, TranscriptSegment

TODAY = datetime.date(2026, 10, 9)
VIDEO = VideoInfo(
    id="abcdefghijk",
    title="A talk",
    channel="A channel",
    duration=3940,
    upload_date="20261008",
    description="About $150 million.",
    chapters=[Chapter(start=0, end=67, title="Intro"), Chapter(start=67, title="Main part")],
)


def segment(start: float, text: str) -> TranscriptSegment:
    return TranscriptSegment(start=start, end=start + 2, text=text)


def transcript(source: str = "yt-dlp-subs: automatic en-orig") -> Transcript:
    return Transcript(
        video_id=VIDEO.id, source=source, language="en", segments=[segment(1.5, "Hello."), segment(67.2, "Next.")]
    )


def test_format_seconds_label() -> None:
    assert format_seconds_label(67.9) == "1:07 | 67s"
    assert format_seconds_label(3723) == "1:02:03 | 3723s"


def test_group_segments_starts_a_block_every_block_seconds() -> None:
    segments = [segment(0, "a"), segment(5, "b"), segment(14.9, "c"), segment(15, "d"), segment(40, "e")]
    assert group_segments(segments, block_seconds=15) == ["[0:00 | 0s] a b c", "[0:15 | 15s] d", "[0:40 | 40s] e"]


def test_group_segments_empty() -> None:
    assert group_segments([]) == []


@pytest.mark.parametrize(
    ("value", "expected"),
    [("20261008", "Oct 8, 2026"), ("20260716", "Jul 16, 2026"), (None, "unknown"), ("2026", "2026")],
)
def test_format_upload_date(value: str | None, expected: str) -> None:
    assert format_upload_date(value) == expected


def test_render_prompt_fills_every_placeholder() -> None:
    template = " | ".join(f"${name}" for name in TEMPLATE_PLACEHOLDERS) + " | $$5"
    prompt = render_prompt(template, VIDEO, transcript(), TODAY)
    assert prompt.startswith(
        "A talk | A channel | https://www.youtube.com/watch?v=abcdefghijk | abcdefghijk | 1:05:40 |"
    )
    assert "Oct 8, 2026 | Oct 9, 2026 | About $150 million." in prompt  # values are not re-scanned for $
    assert "- [0:00 | 0s] Intro\n- [1:07 | 67s] Main part" in prompt
    assert "automatic captions" in prompt
    assert "[0:01 | 1s] Hello.\n[1:07 | 67s] Next." in prompt
    assert prompt.endswith("| $5")


def test_render_prompt_defaults_for_missing_details() -> None:
    video = VideoInfo(id="abcdefghijk", title="T")
    prompt = render_prompt("$channel/$duration/$upload_date/$description/$chapters", video, transcript(), TODAY)
    assert prompt == "unknown channel/unknown/unknown/(no description)/(none)"


def test_render_prompt_human_made_note() -> None:
    assert "written by people" in render_prompt(
        "$transcript_note", VIDEO, transcript("yt-dlp-subs: human-made en"), TODAY
    )


def test_render_prompt_unknown_placeholder() -> None:
    with pytest.raises(GuideError, match=r"Unknown placeholder \$titel"):
        _ = render_prompt("$titel", VIDEO, transcript(), TODAY)


def test_render_prompt_stray_dollar() -> None:
    with pytest.raises(GuideError, match=r"Write a literal \$ as \$\$"):
        _ = render_prompt("costs $5", VIDEO, transcript(), TODAY)


def test_default_template_renders_completely() -> None:
    prompt = render_prompt(default_template(), VIDEO, transcript(), TODAY)
    assert re.search(r"\$[A-Za-z_]", prompt) is None
    assert "watch?v=abcdefghijk&t=<seconds>s" in prompt


def link(label: str, seconds: int, video_id: str = "abcdefghijk") -> str:
    return f"[{label}](https://www.youtube.com/watch?v={video_id}&t={seconds}s)"


def test_check_timestamp_links_relabels_from_seconds() -> None:
    markdown = f"{link('0:12', 12)} to {link('1:10', 67)}, later {link('62:03', 3723)} and {link('9:99', 4000)}"
    result = check_timestamp_links(markdown, VIDEO)
    assert (
        result.markdown
        == f"{link('0:12', 12)} to {link('1:07', 67)}, later {link('1:02:03', 3723)} and {link('1:06:40', 4000)}"
    )
    assert (result.links, result.relabeled) == (4, 3)
    assert result.out_of_range == [4000]  # the video is 3940 seconds long


def test_check_timestamp_links_ignores_other_links() -> None:
    markdown = f"{link('1:10', 67, video_id='zzzzzzzzzzz')} [docs](https://example.com)"
    result = check_timestamp_links(markdown, VIDEO)
    assert result.markdown == markdown
    assert result.links == 0


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("\n# Guide\n\nText\n\n", "# Guide\n\nText\n"),
        ("```markdown\n# Guide\n```", "# Guide\n"),
        ("```\n# Guide\n\n```", "# Guide\n"),
        ("# Guide\n\n```sh\ncode\n```", "# Guide\n\n```sh\ncode\n```\n"),
    ],
)
def test_clean_output(reply: str, expected: str) -> None:
    assert clean_output(reply) == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("The Chief AI Officer Role", "Watching Guide The Chief AI Officer Role.md"),
        ("Bonus Episode: October 8, 2026", "Watching Guide Bonus Episode October 8, 2026.md"),
        ('A/B "test"?  <now>|', "Watching Guide A B test now.md"),
        ("???", "Watching Guide abcdefghijk.md"),
    ],
)
def test_guide_filename(title: str, expected: str) -> None:
    assert guide_filename(VideoInfo(id="abcdefghijk", title=title)) == expected


def test_guide_filename_is_bounded() -> None:
    assert len(guide_filename(VideoInfo(id="abcdefghijk", title="x" * 500))) <= 150
