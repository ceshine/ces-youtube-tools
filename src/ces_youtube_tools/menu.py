"""Parse answers to the numbered menus of interactive commands."""

QUIT_ANSWERS = frozenset({"q", "quit"})


class QuitMenu(Exception):
    """Raised when the user asks to leave a menu."""


def parse_choice(answer: str, count: int, default: int | None = None) -> int:
    """Turn a menu answer into a 1-based item number.

    Args:
        answer (str): What the user typed.
        count (int): Number of items in the menu.
        default (int | None): Item chosen by an empty answer, or None if an answer is required.

    Returns:
        int: The chosen item number, from 1 to `count`.

    Raises:
        QuitMenu: If the answer is `q` or `quit`.
        ValueError: If the answer is not a number from 1 to `count`. The message says what is expected.
    """
    text = answer.strip().lower()
    if text in QUIT_ANSWERS:
        raise QuitMenu
    if not text and default is not None:
        return default
    if text.isdigit() and 1 <= int(text) <= count:
        return int(text)
    raise ValueError(f"Enter a number from 1 to {count}, or q to quit.")
