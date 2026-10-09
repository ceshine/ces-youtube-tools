"""Pure helpers that format values for terminal output."""


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
