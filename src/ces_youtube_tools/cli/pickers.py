"""Arrow-key menus for interactive commands, built on questionary.

The menus need a real terminal, so commands that use them are tested with these functions replaced by fakes. They
return item numbers and know nothing about what the items are.
"""

import sys

import questionary


class NotATerminalError(Exception):
    """Raised when a menu is shown while stdin or stdout is not a terminal."""


def require_terminal() -> None:
    """Check that the menus can be drawn and answered.

    The pickers check this themselves. Commands call it first to fail before doing any work.

    Returns:
        None

    Raises:
        NotATerminalError: If stdin or stdout is not a terminal.
    """
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise NotATerminalError("Interactive menus need a terminal.")


def _items(options: list[str], checked: set[int] | None = None) -> list[questionary.Choice]:
    """Turn option labels into menu items whose values are 1-based item numbers.

    The numbers are stored as strings because questionary's type hints expect string values.

    Args:
        options (list[str]): The labels, in menu order.
        checked (set[int] | None): 1-based numbers of the items shown ticked in a checkbox list.

    Returns:
        list[questionary.Choice]: The menu items.

    Raises:
        ValueError: If there are no options (an empty checkbox list would refuse every answer until Ctrl+C), or a
            checked number is not an item.
    """
    if not options:
        raise ValueError("A menu needs at least one item.")
    checked = checked or set()
    not_items = sorted(checked - set(range(1, len(options) + 1)))
    if not_items:
        raise ValueError(f"Checked item numbers {not_items} are not between 1 and {len(options)}.")
    return [
        questionary.Choice(title=option, value=str(number), checked=number in checked)
        for number, option in enumerate(options, start=1)
    ]


def _require_selection(selected: list[str]) -> bool | str:
    """Validate a checkbox answer.

    Args:
        selected (list[str]): The selected item numbers, as strings.

    Returns:
        bool | str: True if at least one item is selected, otherwise the hint to show.
    """
    return True if selected else "Select at least one item with Space."


def _accept_any(selected: list[str]) -> bool:
    """Accept any checkbox answer, including an empty one.

    Args:
        selected (list[str]): The selected item numbers, as strings.

    Returns:
        bool: Always True.
    """
    return True


def pick_one(message: str, options: list[str], default: int | None = None) -> int | None:
    """Show a list and let the user pick one item with the arrow keys and Enter.

    Args:
        message (str): The question.
        options (list[str]): The item labels, one line each.
        default (int | None): 1-based number of the item the cursor starts on, or None for the first.

    Returns:
        int | None: The chosen 1-based item number, or None if the user pressed Ctrl+C.

    Raises:
        NotATerminalError: If stdin or stdout is not a terminal.
        ValueError: If there are no options.
    """
    require_terminal()
    question = questionary.select(
        message, choices=_items(options), default=str(default) if default is not None else None
    )
    try:
        answer: str = question.unsafe_ask()
    except KeyboardInterrupt:
        return None
    return int(answer)


def pick_many(
    message: str, options: list[str], checked: set[int] | None = None, require_selection: bool = True
) -> list[int] | None:
    """Show a checkbox list and let the user pick items.

    Space toggles an item, `a` toggles all, `i` inverts the selection and Enter confirms. An empty selection is
    refused with a hint, unless `require_selection` is False.

    Args:
        message (str): The question.
        options (list[str]): The item labels, one line each.
        checked (set[int] | None): 1-based numbers of the items shown ticked at the start, e.g. earlier picks.
        require_selection (bool): Whether at least one item must be ticked to confirm.

    Returns:
        list[int] | None: The chosen 1-based item numbers in menu order, or None if the user pressed Ctrl+C.

    Raises:
        NotATerminalError: If stdin or stdout is not a terminal.
        ValueError: If there are no options, or a checked number is not an item.
    """
    require_terminal()
    validate = _require_selection if require_selection else _accept_any
    question = questionary.checkbox(message, choices=_items(options, checked), validate=validate)
    try:
        answer: list[str] = question.unsafe_ask()
    except KeyboardInterrupt:
        return None
    return sorted(int(number) for number in answer)
