"""Read and write the YAML front matter at the top of a guide: video metadata written by the tool, not the model."""

import datetime

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

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
    know (for example, tags added in a notes app) are ignored.

    Attributes:
        video_id (str | None): YouTube video ID.
        title (str | None): The video's title on YouTube.
        channel (str | None): Channel name.
        channel_id (str | None): Channel ID.
        upload_date (datetime.date | None): Upload date.
        duration (int | None): Length in seconds.
        members_only (bool | None): Whether only channel members can watch the video. None if unknown.
        generated (datetime.date | None): The date the guide was written.
    """

    model_config = ConfigDict(extra="ignore")

    video_id: str | None = None
    title: str | None = None
    channel: str | None = None
    channel_id: str | None = None
    upload_date: datetime.date | None = None
    duration: int | None = None
    members_only: bool | None = None
    generated: datetime.date | None = None


def metadata_from_video(video: VideoInfo, generated: datetime.date) -> GuideMetadata:
    """Build a guide's metadata from the video's.

    Args:
        video (VideoInfo): The video.
        generated (datetime.date): The date the guide is written.

    Returns:
        GuideMetadata: The metadata. `members_only` is None when yt-dlp did not report the video's availability.
    """
    return GuideMetadata(
        video_id=video.id,
        title=video.title,
        channel=video.channel,
        channel_id=video.channel_id,
        upload_date=parse_upload_date(video.upload_date),
        duration=round(video.duration) if video.duration is not None else None,
        members_only=video.availability == "subscriber_only" if video.availability is not None else None,
        generated=generated,
    )


def render_front_matter(metadata: GuideMetadata) -> str:
    """Write metadata as a front matter block.

    Args:
        metadata (GuideMetadata): The metadata. Unknown values are written as `null`, so every guide has the same keys.

    Returns:
        str: The block between `---` lines, followed by a blank line.
    """
    body = yaml.safe_dump(metadata.model_dump(), sort_keys=False, allow_unicode=True, width=_YAML_WIDTH)
    return f"{_DELIMITER}\n{body}{_DELIMITER}\n\n"


def split_front_matter(text: str) -> tuple[GuideMetadata, str]:
    """Separate a guide's front matter from its Markdown.

    Front matter starts at a `---` first line and ends at the next `---` line. A guide without it, or whose block is
    never closed, is all Markdown.

    Args:
        text (str): The guide file's text.

    Returns:
        tuple[GuideMetadata, str]: The metadata (all fields None if there is no front matter) and the Markdown,
            without the blank lines that followed the block.

    Raises:
        FrontMatterError: If the block is not valid YAML, is not a set of `key: value` lines, or has a value of the
            wrong type.
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip() != _DELIMITER:
        return GuideMetadata(), text
    end = next((index for index in range(1, len(lines)) if lines[index].rstrip() == _DELIMITER), None)
    if end is None:
        return GuideMetadata(), text
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
    return metadata, "".join(lines[end + 1 :]).lstrip("\n")
