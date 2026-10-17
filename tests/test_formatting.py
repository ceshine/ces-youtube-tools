import pytest
from rich.cells import cell_len

from ces_youtube_tools.youtube import VideoSummary
from ces_youtube_tools.formatting import fit_cells, format_age, format_duration, video_choice_label

NOW = 1_800_000_000


@pytest.mark.parametrize(
    ("seconds", "expected"), [(None, ""), (0, "0:00"), (59.9, "0:59"), (815, "13:35"), (3940, "1:05:40")]
)
def test_format_duration(seconds: float | None, expected: str) -> None:
    assert format_duration(seconds) == expected


@pytest.mark.parametrize(
    ("age_seconds", "expected"),
    [
        (None, ""),
        (-60, "<1h ago"),
        (600, "<1h ago"),
        (2 * 3600, "2h ago"),
        (3 * 86400, "3d ago"),
        (65 * 86400, "2mo ago"),
        (800 * 86400, "2y ago"),
    ],
)
def test_format_age(age_seconds: int | None, expected: str) -> None:
    timestamp = None if age_seconds is None else NOW - age_seconds
    assert format_age(timestamp, NOW) == expected


@pytest.mark.parametrize(
    ("text", "width", "expected"),
    [
        ("short", 10, "short"),
        ("exactly10!", 10, "exactly10!"),
        ("a long title", 7, "a long…"),
        ("日本語の題", 6, "日本…"),
    ],
)
def test_fit_cells(text: str, width: int, expected: str) -> None:
    assert fit_cells(text, width) == expected
    assert cell_len(fit_cells(text, width)) <= width


@pytest.mark.parametrize("width", [0, -3])
def test_fit_cells_rejects_widths_below_1(width: int) -> None:
    with pytest.raises(ValueError, match="width must be at least 1"):
        _ = fit_cells("text", width)


def test_video_choice_label_aligns_the_columns() -> None:
    plain = VideoSummary(id="aaaaaaaaaaa", title="First video", duration=300, timestamp=NOW - 2 * 86400)
    tagged = VideoSummary(
        id="bbbbbbbbbbb",
        title="Bonus episode",
        duration=3940,
        timestamp=NOW - 3 * 3600,
        live_status="was_live",
        members_only=True,
    )
    assert video_choice_label(plain, NOW, 80) == "  2d ago      5:00                  First video"
    assert video_choice_label(tagged, NOW, 80) == "  3h ago   1:05:40  members stream  Bonus episode"


def test_video_choice_label_cuts_the_title_to_the_width() -> None:
    video = VideoSummary(id="aaaaaaaaaaa", title="A very long title that does not fit")
    assert video_choice_label(video, NOW, 50) == " " * 8 + "  " + " " * 8 + "  " + " " * 14 + "  " + "A very long t…"
    assert video_choice_label(video, NOW, 20).endswith("  A very lo…")  # never shorter than MIN_TITLE_WIDTH
