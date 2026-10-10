import pytest

from ces_youtube_tools.formatting import format_age, format_duration

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
