"""The paged video multi-select of `browse`: a channel's listing a page at a time, keeping the selection by ID."""

import time
import shutil

from .common import CONSOLE, print_error
from .pickers import pick_many
from ..ytdlp import YtDlpError
from ..youtube import VideoPager, VideoSummary
from ..formatting import video_choice_label

# The cells questionary's checkbox draws before each item's label: " » " (or three spaces) and "○ " or "● ". See
# "questionary's checkbox draws five cells before each label" in docs/implementation_notes.md.
_MENU_MARK_WIDTH = 5
_NEXT_PAGE_LABEL = "▸ Next page (older videos)"


def _video_range(pager: VideoPager, number: int) -> tuple[int, int]:
    """Find where a fetched page's videos sit in the whole listing.

    Args:
        pager (VideoPager): The listing.
        number (int): The page's number. The page and every page before it must be fetched.

    Returns:
        tuple[int, int]: The 1-based positions of the page's first and last videos.
    """
    first = sum(len(pager.page(earlier)) for earlier in range(1, number)) + 1
    return first, first + len(pager.page(number)) - 1


def _video_menu_title(pager: VideoPager, number: int, channel_name: str, elsewhere: int) -> str:
    """Build the title of a page's video menu.

    Args:
        pager (VideoPager): The listing.
        number (int): The page's number. The page must be fetched.
        channel_name (str): The channel's name.
        elsewhere (int): Number of videos selected on other pages.

    Returns:
        str: The title, e.g. `Videos 21–40 of NAME (3 selected on other pages) (Space selects, Enter confirms)`.
    """
    if number == 1:
        title = f"Latest videos of {channel_name}"
    else:
        first, last = _video_range(pager, number)
        title = f"Videos {first}–{last} of {channel_name}"
    if elsewhere:
        title += f" ({elsewhere} selected on other pages)"
    return f"{title} (Space selects, Enter confirms)"


def _fetch_next_page(pager: VideoPager, channel_name: str) -> bool:
    """Fetch the page after the last fetched one, and say why not when it has no videos.

    Args:
        pager (VideoPager): The listing.
        channel_name (str): The channel's name, for the status line.

    Returns:
        bool: True if the new page has videos. False if it is empty or yt-dlp failed; the pager is then unchanged
            apart from `has_more`, so the current page can be shown again.

    Raises:
        KeyboardInterrupt: If the user pressed Ctrl+C while the page was loading. The pager is unchanged.
    """
    try:
        with CONSOLE.status(f"Fetching older videos of {channel_name}..."):
            older = pager.page(pager.page_count + 1)
    except YtDlpError as exc:
        print_error(str(exc))
        return False
    if not older:
        CONSOLE.print("No older videos.")
        return False
    return True


def pick_videos(pager: VideoPager, channel_name: str) -> list[VideoSummary] | None:
    """Let the user pick videos from a channel's listing, a page at a time.

    After the videos, a page's menu lists "Previous page" on pages after the first, and "Next page" when there may be
    older videos. Ticking one of them moves to that page. The selection is kept by video ID across pages: videos
    ticked on a page stay selected when leaving it, a page shows its earlier ticks again, and unticking a video there
    drops it. Enter with neither page entry ticked confirms.

    Args:
        pager (VideoPager): The listing. Its first page must already be fetched and hold videos.
        channel_name (str): The channel's name, for the menu titles.

    Returns:
        list[VideoSummary] | None: The selected videos, newest first across pages, or None if the user pressed
            Ctrl+C in a menu or while a page was loading.
    """
    width = shutil.get_terminal_size().columns - _MENU_MARK_WIDTH
    now = time.time()
    selected: dict[str, VideoSummary] = {}
    number = 1
    while True:
        videos = pager.page(number)
        labels = [video_choice_label(video, now, width) for video in videos]
        previous_entry: int | None = None
        next_entry: int | None = None
        if number > 1:
            first, last = _video_range(pager, number - 1)
            labels.append(f"◂ Previous page (videos {first}–{last})")
            previous_entry = len(labels)
        if number < pager.page_count or pager.has_more:
            labels.append(_NEXT_PAGE_LABEL)
            next_entry = len(labels)
        on_page = {video.id for video in videos}
        elsewhere = sum(1 for video_id in selected if video_id not in on_page)
        answer = pick_many(
            _video_menu_title(pager, number, channel_name, elsewhere),
            labels,
            checked={item for item, video in enumerate(videos, start=1) if video.id in selected},
            require_selection=elsewhere == 0,
        )
        if answer is None:
            return None
        # Update the selection first, so videos ticked together with a page entry stay selected.
        for item, video in enumerate(videos, start=1):
            if item in answer:
                selected[video.id] = video
            else:
                selected.pop(video.id, None)
        go_previous = previous_entry in answer
        go_next = next_entry in answer
        if go_previous and go_next:
            CONSOLE.print(
                "Tick one page entry to change pages, or neither to confirm. Note that `a` and `i` tick them too.",
                style="yellow",
            )
        elif go_previous:
            number -= 1
        elif go_next:
            try:
                if number < pager.page_count or _fetch_next_page(pager, channel_name):
                    number += 1
            except KeyboardInterrupt:  # Ctrl+C while a page loads quits, as it does in a menu
                return None
        else:
            return [video for video in pager.fetched() if video.id in selected]
