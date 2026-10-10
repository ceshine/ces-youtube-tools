"""Build the guide prompt, and check and save the generated guide. Independent of the generator that writes it."""

import re
import string
import datetime
from pathlib import Path
from importlib import resources
from dataclasses import dataclass
from typing import Protocol

from .video import VideoInfo
from .formatting import format_duration
from .transcript import Transcript, TranscriptSegment

DEFAULT_BLOCK_SECONDS = 15
# Placeholders a prompt template may use, with what they hold.
TEMPLATE_PLACEHOLDERS = {
    "title": "video title",
    "channel": "channel name",
    "url": "video URL",
    "video_id": "video ID",
    "duration": "video length, e.g. 1:05:40",
    "upload_date": "upload date, e.g. Oct 8, 2026",
    "today": "the date the guide is written",
    "description": "video description",
    "chapters": "uploader-set chapters as a list, or a note that there are none",
    "transcript_note": "a sentence on where the transcript comes from and how reliable it is",
    "transcript": "the transcript, in blocks starting with `[m:ss | Ns]`",
}
_UNSAFE_FILENAME_CHARACTERS = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')
_CODE_FENCE = re.compile(r"^```[\w-]*\n(.*)\n```$", re.DOTALL)


class GuideError(Exception):
    """Raised when a guide cannot be generated, or the prompt template is invalid."""


class GuideGenerator(Protocol):
    """Writes a Markdown guide from a prompt, e.g. with the `claude` CLI or an LLM API."""

    def generate(self, prompt: str, video: VideoInfo, work_dir: Path) -> str:
        """Generate the guide.

        Args:
            prompt (str): The full prompt, including the transcript.
            video (VideoInfo): The video the guide is for.
            work_dir (Path): The video's work directory.

        Returns:
            str: The generated Markdown.

        Raises:
            GuideError: If generation fails.
        """
        ...


def default_template() -> str:
    """Load the prompt template that ships with the package.

    Returns:
        str: The template text.
    """
    return resources.files("ces_youtube_tools").joinpath("templates/guide_prompt.md").read_text(encoding="utf-8")


def format_seconds_label(seconds: float) -> str:
    """Format a time as it appears in transcript blocks: `m:ss | Ns`.

    Args:
        seconds (float): The time in seconds.

    Returns:
        str: For example `1:07 | 67s`.
    """
    return f"{format_duration(int(seconds))} | {int(seconds)}s"


def group_segments(segments: list[TranscriptSegment], block_seconds: int = DEFAULT_BLOCK_SECONDS) -> list[str]:
    """Join transcript segments into blocks of about `block_seconds`, each starting with its time.

    Blocks keep the prompt short while leaving enough timestamps to link to.

    Args:
        segments (list[TranscriptSegment]): Segments in time order.
        block_seconds (int): Start a new block once the current one spans this many seconds.

    Returns:
        list[str]: Lines such as `[1:07 | 67s] text text ...`.
    """
    blocks: list[str] = []
    block_start: float | None = None
    texts: list[str] = []
    for segment in segments:
        if block_start is not None and segment.start - block_start >= block_seconds:
            blocks.append(f"[{format_seconds_label(block_start)}] {' '.join(texts)}")
            block_start, texts = None, []
        if block_start is None:
            block_start = segment.start
        texts.append(segment.text)
    if block_start is not None:
        blocks.append(f"[{format_seconds_label(block_start)}] {' '.join(texts)}")
    return blocks


def format_upload_date(upload_date: str | None) -> str:
    """Format a yt-dlp upload date (`YYYYMMDD`) like `Oct 8, 2026`.

    Args:
        upload_date (str | None): The date, or None if unknown.

    Returns:
        str: The formatted date, `unknown`, or the raw value if it is malformed.
    """
    if not upload_date:
        return "unknown"
    try:
        return format_date(datetime.date.fromisoformat(upload_date))  # accepts YYYYMMDD
    except ValueError:
        return upload_date


def format_date(date: datetime.date) -> str:
    """Format a date like `Oct 8, 2026`.

    Args:
        date (datetime.date): The date.

    Returns:
        str: The formatted date.
    """
    return f"{date:%b} {date.day}, {date.year}"


def _transcript_note(transcript: Transcript) -> str:
    """Describe the transcript's source for the prompt.

    Args:
        transcript (Transcript): The transcript.

    Returns:
        str: One sentence for the `$transcript_note` placeholder.
    """
    if "automatic" in transcript.source:
        return (
            "The transcript comes from automatic captions and may misspell names and terms. Correct a misspelling, "
            "in quotes too, only when the right spelling is clear from the title, description or context."
        )
    return "The transcript comes from subtitles written by people."


def render_prompt(template: str, video: VideoInfo, transcript: Transcript, today: datetime.date) -> str:
    """Fill a prompt template with a video's details and transcript.

    Args:
        template (str): Template text with `$placeholder`s (see `TEMPLATE_PLACEHOLDERS`). `$$` is a literal `$`.
        video (VideoInfo): The video.
        transcript (Transcript): Its transcript.
        today (datetime.date): The date the guide is written.

    Returns:
        str: The prompt.

    Raises:
        GuideError: If the template uses an unknown placeholder or a stray `$`.
    """
    chapters = "\n".join(f"- [{format_seconds_label(chapter.start)}] {chapter.title}" for chapter in video.chapters)
    values = {
        "title": video.title,
        "channel": video.channel or "unknown channel",
        "url": video.url,
        "video_id": video.id,
        "duration": format_duration(video.duration) or "unknown",
        "upload_date": format_upload_date(video.upload_date),
        "today": format_date(today),
        "description": video.description.strip() or "(no description)",
        "chapters": chapters or "(none)",
        "transcript_note": _transcript_note(transcript),
        "transcript": "\n".join(group_segments(transcript.segments)),
    }
    try:
        return string.Template(template).substitute(values)
    except KeyError as exc:
        known = ", ".join(f"${name}" for name in TEMPLATE_PLACEHOLDERS)
        raise GuideError(f"Unknown placeholder ${exc.args[0]} in the prompt template. Known: {known}.") from exc
    except ValueError as exc:
        raise GuideError(f"Invalid prompt template: {exc}. Write a literal $ as $$.") from exc


@dataclass(frozen=True)
class LinkCheck:
    """The result of checking a guide's timestamp links.

    Attributes:
        markdown (str): The guide with every timestamp label rewritten to match its link's seconds.
        links (int): Number of timestamp links found.
        relabeled (int): Number of labels that did not match their seconds and were rewritten.
        out_of_range (list[int]): Link times (seconds) past the end of the video.
    """

    markdown: str
    links: int
    relabeled: int
    out_of_range: list[int]


def check_timestamp_links(markdown: str, video: VideoInfo) -> LinkCheck:
    """Make every timestamp label agree with its link, whose seconds value is authoritative.

    Args:
        markdown (str): The generated guide.
        video (VideoInfo): The video the links point to.

    Returns:
        LinkCheck: The corrected guide and what was found.
    """
    pattern = re.compile(
        r"\[(\d+:\d{2}(?::\d{2})?)\]\((https://www\.youtube\.com/watch\?v=" + re.escape(video.id) + r"&t=(\d+)s)\)"
    )
    counts = {"links": 0, "relabeled": 0}
    out_of_range: list[int] = []

    def relabel(match: re.Match[str]) -> str:
        seconds = int(match.group(3))
        label = format_duration(seconds)
        counts["links"] += 1
        if label != match.group(1):
            counts["relabeled"] += 1
        if video.duration is not None and seconds > video.duration:
            out_of_range.append(seconds)
        return f"[{label}]({match.group(2)})"

    fixed = pattern.sub(relabel, markdown)
    return LinkCheck(markdown=fixed, links=counts["links"], relabeled=counts["relabeled"], out_of_range=out_of_range)


def clean_output(markdown: str) -> str:
    """Remove a code fence wrapped around the whole reply, and surrounding whitespace.

    Args:
        markdown (str): The generator's reply.

    Returns:
        str: The Markdown, ending with one newline.
    """
    text = markdown.strip()
    match = _CODE_FENCE.match(text)
    if match is not None:
        text = match.group(1).strip()
    return text + "\n"


def guide_filename(video: VideoInfo) -> str:
    """Build the guide's file name from the video title.

    Args:
        video (VideoInfo): The video.

    Returns:
        str: For example `Watching Guide The Chief AI Officer Role.md`, without characters that are unsafe in file
            names, and at most about 150 characters long.
    """
    title = " ".join(_UNSAFE_FILENAME_CHARACTERS.sub(" ", video.title).split())[:120].rstrip(" .") or video.id
    return f"Watching Guide {title}.md"
