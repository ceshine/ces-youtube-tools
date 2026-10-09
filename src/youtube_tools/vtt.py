"""Parse WebVTT subtitles into transcript segments.

YouTube's automatic captions roll: each cue repeats the previous line as plain text above the new line (which carries
word-level timing tags), and about 10 ms filler cues repeat the line just shown. Human-made subtitles have no repeats.
One rule covers both: drop the lines at the start of a cue that repeat the line emitted last, and keep the rest.
See docs/implementation_notes.md.
"""

import re
import html

from .transcript import TranscriptSegment

_TIMING_PATTERN = re.compile(r"^(\S+)\s+-->\s+(\S+)")
_TAG_PATTERN = re.compile(r"<[^>]*>")


def parse_timestamp(value: str) -> float:
    """Convert a WebVTT timestamp to seconds.

    Args:
        value (str): `HH:MM:SS.mmm` or `MM:SS.mmm`.

    Returns:
        float: The time in seconds.

    Raises:
        ValueError: If the timestamp is malformed.
    """
    parts = value.split(":")
    if len(parts) not in (2, 3):
        raise ValueError(f"Malformed WebVTT timestamp: {value!r}")
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + float(part)
    return seconds


def _clean_line(line: str) -> str:
    """Remove markup from a cue text line.

    Args:
        line (str): The raw line.

    Returns:
        str: The line without tags and entities, with surrounding whitespace removed.
    """
    return html.unescape(_TAG_PATTERN.sub("", line)).strip()


def parse_vtt(text: str) -> list[TranscriptSegment]:
    """Parse WebVTT subtitles, removing the repeated lines of rolling captions.

    Cues are separated by blank lines. A line holding only a space belongs to its cue, as in YouTube's automatic
    captions, so only truly empty lines separate cues.

    Args:
        text (str): The WebVTT file content.

    Returns:
        list[TranscriptSegment]: One segment per cue that adds new text, in file order.

    Raises:
        ValueError: If a cue has a malformed timestamp.
    """
    segments: list[TranscriptSegment] = []
    last_line: str | None = None
    for block in text.replace("\r\n", "\n").split("\n\n"):
        lines = block.split("\n")
        timing_index = next((index for index, line in enumerate(lines) if "-->" in line), None)
        if timing_index is None:
            continue  # header, NOTE, STYLE or REGION block
        match = _TIMING_PATTERN.match(lines[timing_index].strip())
        if match is None:
            continue
        cue_lines = [cleaned for line in lines[timing_index + 1 :] if (cleaned := _clean_line(line))]
        while cue_lines and cue_lines[0] == last_line:
            cue_lines.pop(0)
        if not cue_lines:
            continue
        segments.append(
            TranscriptSegment(
                start=parse_timestamp(match.group(1)), end=parse_timestamp(match.group(2)), text=" ".join(cue_lines)
            )
        )
        last_line = cue_lines[-1]
    return segments
