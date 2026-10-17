from typing import Any

import pytest

from ces_youtube_tools import pickers
from ces_youtube_tools.pickers import pick_one, pick_many, NotATerminalError


class FakeQuestion:
    """Stands in for a questionary question: returns a set answer or raises."""

    def __init__(self, answer: Any) -> None:
        self.answer = answer

    def unsafe_ask(self) -> Any:
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer


@pytest.fixture
def terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pickers.sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(pickers.sys.stdout, "isatty", lambda: True, raising=False)


@pytest.fixture
def asked(terminal: None, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace questionary's prompts and record how they were called. Set `answer` before calling a picker."""
    calls: dict[str, Any] = {"answer": None}

    def fake(kind: str):
        def prompt(message: str, choices: list[Any], **kwargs: Any) -> FakeQuestion:
            calls[kind] = {"message": message, "choices": choices, **kwargs}
            return FakeQuestion(calls["answer"])

        return prompt

    monkeypatch.setattr(pickers.questionary, "select", fake("select"))
    monkeypatch.setattr(pickers.questionary, "checkbox", fake("checkbox"))
    return calls


def test_pick_one_numbers_the_items_and_passes_the_default(asked: dict[str, Any]) -> None:
    asked["answer"] = "2"
    assert pick_one("Channel", ["One", "Two", "Three"], default=3) == 2
    choices = asked["select"]["choices"]
    assert [(c.title, c.value) for c in choices] == [("One", "1"), ("Two", "2"), ("Three", "3")]
    assert asked["select"]["default"] == "3"


def test_pick_many_returns_the_numbers_in_menu_order(asked: dict[str, Any]) -> None:
    asked["answer"] = ["3", "1"]
    assert pick_many("Videos", ["One", "Two", "Three"]) == [1, 3]
    validate = asked["checkbox"]["validate"]
    assert validate(["2"]) is True
    assert validate([]) == "Select at least one item with Space."


@pytest.mark.parametrize("pick", [lambda: pick_one("Channel", ["One"]), lambda: pick_many("Videos", ["One"])])
def test_ctrl_c_returns_none(asked: dict[str, Any], pick: Any) -> None:
    asked["answer"] = KeyboardInterrupt()
    assert pick() is None


@pytest.mark.parametrize("pick", [lambda: pick_one("Channel", []), lambda: pick_many("Videos", [])])
def test_pickers_need_at_least_one_item(asked: dict[str, Any], pick: Any) -> None:
    with pytest.raises(ValueError, match="A menu needs at least one item."):
        _ = pick()


@pytest.mark.parametrize("stream", ["stdin", "stdout"])
@pytest.mark.parametrize("pick", [lambda: pick_one("Channel", ["One"]), lambda: pick_many("Videos", ["One"])])
def test_pickers_need_a_terminal(terminal: None, monkeypatch: pytest.MonkeyPatch, stream: str, pick: Any) -> None:
    monkeypatch.setattr(getattr(pickers.sys, stream), "isatty", lambda: False, raising=False)
    with pytest.raises(NotATerminalError, match="Interactive menus need a terminal."):
        _ = pick()
