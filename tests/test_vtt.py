from pathlib import Path

import pytest

from ces_youtube_tools.vtt import parse_vtt, parse_timestamp
from ces_youtube_tools.transcript import TranscriptSegment

FIXTURES = Path(__file__).parent / "fixtures"

# YouTube's automatic captions put lines holding a single space inside cues. Built in code rather than kept as a
# fixture file, because the trailing-whitespace pre-commit hook would turn them into cue-separating empty lines.
SPACE_LINE = " "


def cue(timing: str, *lines: str) -> str:
    return "\n".join([f"{timing} align:start position:0%", *lines])


# Synthetic text in the layout of a real automatic caption file: each cue repeats the previous line above the new
# one (which has word timing tags), and 10 ms filler cues repeat the line just shown.
AUTO_CAPTIONS = (
    "\n\n".join(
        [
            "WEBVTT\nKind: captions\nLanguage: en",
            cue("00:00:01.000 --> 00:00:03.000", SPACE_LINE, "[music]"),
            cue(
                "00:00:03.000 --> 00:00:05.000",
                SPACE_LINE,
                "Welcome<00:00:03.400><c> back</c><00:00:03.800><c> to</c><00:00:04.200><c> the</c><00:00:04.600><c> show.</c>",
            ),
            cue("00:00:05.000 --> 00:00:05.010", "Welcome back to the show.", SPACE_LINE),
            cue(
                "00:00:05.010 --> 00:00:07.000",
                "Welcome back to the show.",
                "Today<00:00:05.400><c> we</c><00:00:05.800><c> talk</c><00:00:06.200><c> about</c>",
            ),
            cue("00:00:07.000 --> 00:00:07.010", "Today we talk about", SPACE_LINE),
            cue("00:00:07.010 --> 00:00:08.000", "Today we talk about", "gardens."),
            cue("00:00:08.000 --> 00:00:08.010", "gardens.", SPACE_LINE),
            cue(
                "00:00:08.010 --> 00:00:10.000",
                "gardens.",
                "&gt;&gt; Sounds<00:00:08.500><c> good</c><00:00:09.000><c> to</c><00:00:09.500><c> me.</c>",
            ),
        ]
    )
    + "\n"
)


def test_parse_timestamp() -> None:
    assert parse_timestamp("00:00:05.680") == pytest.approx(5.68)
    assert parse_timestamp("01:02:03.500") == pytest.approx(3723.5)
    assert parse_timestamp("02:03.250") == pytest.approx(123.25)


@pytest.mark.parametrize("value", ["5.0", "1:2:3:4", "aa:bb"])
def test_parse_timestamp_rejects_malformed(value: str) -> None:
    with pytest.raises(ValueError):
        _ = parse_timestamp(value)


def test_automatic_captions_drop_rolling_repeats() -> None:
    segments = parse_vtt(AUTO_CAPTIONS)
    assert segments == [
        TranscriptSegment(start=1.0, end=3.0, text="[music]"),
        TranscriptSegment(start=3.0, end=5.0, text="Welcome back to the show."),
        TranscriptSegment(start=5.01, end=7.0, text="Today we talk about"),
        # A short final word comes without timing tags but in the same rolling layout.
        TranscriptSegment(start=7.01, end=8.0, text="gardens."),
        # Entities are decoded: ">>" is YouTube's speaker-change marker.
        TranscriptSegment(start=8.01, end=10.0, text=">> Sounds good to me."),
    ]


def test_human_made_subtitles_keep_every_line() -> None:
    segments = parse_vtt((FIXTURES / "manual_subtitles.vtt").read_text(encoding="utf-8"))
    assert segments == [
        TranscriptSegment(start=0.0, end=4.5, text="The first line of a human-made subtitle,"),
        TranscriptSegment(start=4.5, end=9.25, text="and the second one, split over two lines."),
        TranscriptSegment(start=9.25, end=3600.0, text="A cue that runs until one hour."),
    ]


def test_windows_line_endings() -> None:
    text = (FIXTURES / "manual_subtitles.vtt").read_text(encoding="utf-8").replace("\n", "\r\n")
    assert len(parse_vtt(text)) == 3


def test_empty_and_header_only() -> None:
    assert parse_vtt("") == []
    assert parse_vtt("WEBVTT\nKind: captions\n") == []


def test_malformed_cue_timestamp_raises() -> None:
    with pytest.raises(ValueError, match="Malformed"):
        _ = parse_vtt("WEBVTT\n\n5.0 --> 6.0\nhello\n")


def test_space_only_line_does_not_split_a_cue() -> None:
    assert "\n \n" in AUTO_CAPTIONS
    assert parse_vtt(AUTO_CAPTIONS)[0].text == "[music]"
