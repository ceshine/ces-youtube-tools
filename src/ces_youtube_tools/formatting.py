"""Pure helpers that format values for terminal output."""

from rich.cells import cell_len, set_cell_size

from .youtube import VideoSummary

# Widths of the fixed columns of a video menu line, in terminal cells.
AGE_WIDTH = 8  # "11mo ago"
LENGTH_WIDTH = 8  # "12:00:00"
KIND_WIDTH = 14  # "members stream"
MIN_TITLE_WIDTH = 10


def format_duration(seconds: float | None) -> str:
    """Format a length in seconds as `H:MM:SS` or `M:SS`.

    Args:
        seconds (float | None): The length, or None if unknown.

    Returns:
        str: The formatted length, or an empty string if unknown.
    """
    if seconds is None:
        return ""
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def format_age(timestamp: int | None, now: float) -> str:
    """Format how long ago a timestamp was, at the coarse precision YouTube listings give.

    Args:
        timestamp (int | None): Unix timestamp, or None if unknown.
        now (float): The current Unix time.

    Returns:
        str: For example `3h ago`, `2d ago` or `5mo ago`, or an empty string if unknown.
    """
    if timestamp is None:
        return ""
    hours = max(0, int(now - timestamp)) // 3600
    if hours < 1:
        return "<1h ago"
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    if days < 30:
        return f"{days}d ago"
    if days < 365:
        return f"{days // 30}mo ago"
    return f"{days // 365}y ago"


def fit_cells(text: str, width: int) -> str:
    """Cut text to fit a number of terminal cells, ending it with `…` when cut.

    Wide characters (for example CJK) take two cells, so the result is measured in cells, not characters.

    Args:
        text (str): The text.
        width (int): The most cells the result may take. At least 1.

    Returns:
        str: The text, or its start followed by `…`.

    Raises:
        ValueError: If `width` is less than 1, which leaves no room even for `…`.
    """
    if width < 1:
        raise ValueError(f"width must be at least 1, got {width}")
    if cell_len(text) <= width:
        return text
    return set_cell_size(text, width - 1).rstrip() + "…"


def video_choice_label(video: VideoSummary, now: float, width: int) -> str:
    """Build a one-line menu label for a video, with aligned columns.

    The columns are the age, the length, the type (members-only, livestream replay) and the title. The title is cut
    to keep the line within `width` cells, but always keeps at least `MIN_TITLE_WIDTH` cells.

    Args:
        video (VideoSummary): The video.
        now (float): The current Unix time, for the age column.
        width (int): The most cells the line should take.

    Returns:
        str: The label.
    """
    kinds = " ".join(
        (["members"] if video.members_only else []) + (["stream"] if video.live_status == "was_live" else [])
    )
    prefix = (
        f"{format_age(video.timestamp, now):>{AGE_WIDTH}}  "
        f"{format_duration(video.duration):>{LENGTH_WIDTH}}  "
        f"{kinds:<{KIND_WIDTH}}  "
    )
    return prefix + fit_cells(video.title, max(MIN_TITLE_WIDTH, width - len(prefix)))
