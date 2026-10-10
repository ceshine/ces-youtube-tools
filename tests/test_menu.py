import pytest

from ces_youtube_tools.menu import QuitMenu, parse_choice


@pytest.mark.parametrize(("answer", "expected"), [("1", 1), (" 3 ", 3), ("", 2)])
def test_parse_choice(answer: str, expected: int) -> None:
    assert parse_choice(answer, count=3, default=2) == expected


@pytest.mark.parametrize("answer", ["q", "Q", " quit "])
def test_parse_choice_quit(answer: str) -> None:
    with pytest.raises(QuitMenu):
        _ = parse_choice(answer, count=3)


@pytest.mark.parametrize("answer", ["0", "4", "-1", "1.5", "two", ""])
def test_parse_choice_rejects(answer: str) -> None:
    with pytest.raises(ValueError, match="Enter a number from 1 to 3, or q to quit."):
        _ = parse_choice(answer, count=3)
