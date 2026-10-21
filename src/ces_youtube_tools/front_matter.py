"""Read and write the YAML front matter at the top of a guide: video metadata written by the tool, not the model."""

import datetime
from dataclasses import dataclass
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, AwareDatetime, ValidationError, field_serializer

from .guide import parse_upload_date
from .video import VideoInfo

_DELIMITER = "---"
# Wide enough that PyYAML never folds a long title onto a second line.
_YAML_WIDTH = 100_000


class FrontMatterError(Exception):
    """Raised when a guide's front matter cannot be read."""


class GuideMetadata(BaseModel):
    """The video metadata kept in a guide's front matter.

    Every field is optional when reading, because a guide may be hand-written or hand-edited. Keys this model does not
    know (for example, tags added in a notes app) are ignored here; `FrontMatter.data` keeps them.

    Attributes:
        video_id (str | None): YouTube video ID.
        title (str | None): The video's title on YouTube.
        channel (str | None): Channel name.
        channel_id (str | None): Channel ID.
        upload_date (datetime.date | None): Upload date, as yt-dlp reports it.
        published (datetime.datetime | None): When the video was published (see `VideoInfo.published`).
        duration (int | None): Length in seconds.
        members_only (bool | None): Whether only channel members can watch the video. None if unknown.
        generated (datetime.datetime | datetime.date | None): When the guide was written. A date in guides written
            before the tool recorded the time.
    """

    model_config = ConfigDict(extra="ignore")

    video_id: str | None = None
    title: str | None = None
    channel: str | None = None
    channel_id: str | None = None
    upload_date: datetime.date | None = None
    published: AwareDatetime | None = None
    duration: int | None = None
    members_only: bool | None = None
    generated: AwareDatetime | datetime.date | None = None

    @field_serializer("published", "generated")
    def _utc_string(self, value: datetime.date | None) -> datetime.date | str | None:
        """Write a time as a UTC string, such as `2026-10-14T16:03:12Z`, instead of PyYAML's `datetime` form.

        Args:
            value (datetime.date | None): The time, a date, or None.

        Returns:
            datetime.date | str | None: The time as a string; a date or None unchanged.
        """
        if isinstance(value, datetime.datetime):
            return value.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        return value


def metadata_from_video(video: VideoInfo, generated: datetime.datetime | None) -> GuideMetadata:
    """Build a guide's metadata from the video's.

    Args:
        video (VideoInfo): The video.
        generated (datetime.datetime | None): When the guide is written (time zone aware), or None if unknown. It is
            kept in UTC, to the second.

    Returns:
        GuideMetadata: The metadata. `members_only` is None when yt-dlp did not report the video's availability.
    """
    return GuideMetadata(
        video_id=video.id,
        title=video.title,
        channel=video.channel,
        channel_id=video.channel_id,
        upload_date=parse_upload_date(video.upload_date),
        published=video.published,
        duration=round(video.duration) if video.duration is not None else None,
        members_only=video.availability == "subscriber_only" if video.availability is not None else None,
        generated=generated.astimezone(datetime.UTC).replace(microsecond=0) if generated is not None else None,
    )


def render_front_matter(metadata: GuideMetadata, extra: dict[Any, Any] | None = None) -> str:
    """Write metadata as a front matter block.

    Args:
        metadata (GuideMetadata): The metadata. Unknown values are written as `null`, so every guide has the same keys.
        extra (dict[Any, Any] | None): Keys `GuideMetadata` does not know, such as tags added in a notes app, written
            after its keys. Keys it knows are ignored here.

    Returns:
        str: The block between `---` lines, followed by a blank line.
    """
    data = metadata.model_dump()
    if extra:
        data.update((key, value) for key, value in extra.items() if key not in GuideMetadata.model_fields)
    body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=_YAML_WIDTH)
    return f"{_DELIMITER}\n{body}{_DELIMITER}\n\n"


def _block_end(lines: list[str]) -> int | None:
    """Find the closing `---` line of a front matter block.

    Args:
        lines (list[str]): The guide's lines.

    Returns:
        int | None: The index of the closing line, or None if the guide has no closed block starting on its first line.
    """
    if not lines or lines[0].rstrip() != _DELIMITER:
        return None
    return next((index for index in range(1, len(lines)) if lines[index].rstrip() == _DELIMITER), None)


@dataclass(frozen=True)
class FrontMatter:
    """A guide split into its front matter and its Markdown.

    Attributes:
        metadata (GuideMetadata): The metadata; all fields None if there is no front matter.
        data (dict[Any, Any] | None): The block as read, including keys `GuideMetadata` does not know, or None if the
            guide has no front matter.
        markdown (str): The Markdown, without the blank lines that followed the block.
    """

    metadata: GuideMetadata
    data: dict[Any, Any] | None
    markdown: str


def read_front_matter(text: str) -> FrontMatter:
    """Separate a guide's front matter from its Markdown, keeping the block as read.

    Front matter starts at a `---` first line and ends at the next `---` line. A guide without it, or whose block is
    never closed, is all Markdown.

    Args:
        text (str): The guide file's text.

    Returns:
        FrontMatter: The metadata, the block as read, and the Markdown.

    Raises:
        FrontMatterError: If the block is not valid YAML, is not a set of `key: value` lines, or has a value of the
            wrong type.
    """
    lines = text.splitlines(keepends=True)
    end = _block_end(lines)
    if end is None:
        return FrontMatter(metadata=GuideMetadata(), data=None, markdown=text)
    try:
        data = yaml.safe_load("".join(lines[1:end]))
    except yaml.MarkedYAMLError as exc:
        if exc.problem_mark is None:
            raise FrontMatterError(f"the front matter is not valid YAML: {exc.problem}") from exc
        # PyYAML counts lines from 0 within the block, which starts on the file's second line.
        line = exc.problem_mark.line + 2
        raise FrontMatterError(f"the front matter is not valid YAML at line {line}: {exc.problem}") from exc
    except yaml.YAMLError as exc:
        raise FrontMatterError(f"the front matter is not valid YAML ({exc})") from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise FrontMatterError("the front matter is not a set of `key: value` lines")
    try:
        metadata = GuideMetadata.model_validate(data)
    except ValidationError as exc:
        raise FrontMatterError(f"invalid front matter: {exc}") from exc
    return FrontMatter(metadata=metadata, data=data, markdown="".join(lines[end + 1 :]).lstrip("\n"))


def split_front_matter(text: str) -> tuple[GuideMetadata, str]:
    """Separate a guide's front matter from its Markdown.

    Args:
        text (str): The guide file's text.

    Returns:
        tuple[GuideMetadata, str]: The metadata (all fields None if there is no front matter) and the Markdown,
            without the blank lines that followed the block (see `read_front_matter`).

    Raises:
        FrontMatterError: If the block is not valid YAML, is not a set of `key: value` lines, or has a value of the
            wrong type.
    """
    front_matter = read_front_matter(text)
    return front_matter.metadata, front_matter.markdown
