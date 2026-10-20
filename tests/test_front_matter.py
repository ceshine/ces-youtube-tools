import datetime

import pytest

from ces_youtube_tools.video import VideoInfo
from ces_youtube_tools.front_matter import (
    GuideMetadata,
    FrontMatterError,
    has_front_matter,
    split_front_matter,
    metadata_from_video,
    render_front_matter,
)

TODAY = datetime.date(2026, 10, 9)
VIDEO = VideoInfo(
    id="abcdefghijk",
    title="A video: part 1",
    channel="A channel",
    channel_id="UCxxxxxxxxxxxxxxxxxxxxxx",
    duration=600.4,
    upload_date="20261008",
    availability="public",
)


def test_metadata_from_video() -> None:
    assert metadata_from_video(VIDEO, TODAY) == GuideMetadata(
        video_id="abcdefghijk",
        title="A video: part 1",
        channel="A channel",
        channel_id="UCxxxxxxxxxxxxxxxxxxxxxx",
        upload_date=datetime.date(2026, 10, 8),
        duration=600,
        members_only=False,
        generated=TODAY,
    )


@pytest.mark.parametrize(
    ("availability", "members_only"), [("subscriber_only", True), ("unlisted", False), (None, None)]
)
def test_metadata_from_video_members_only(availability: str | None, members_only: bool | None) -> None:
    video = VIDEO.model_copy(update={"availability": availability})
    assert metadata_from_video(video, TODAY).members_only is members_only


@pytest.mark.parametrize("upload_date", [None, "", "2026-10-08", "20261399"])
def test_metadata_from_video_leaves_out_a_bad_upload_date(upload_date: str | None) -> None:
    video = VIDEO.model_copy(update={"upload_date": upload_date})
    assert metadata_from_video(video, TODAY).upload_date is None


def test_render_front_matter() -> None:
    assert render_front_matter(metadata_from_video(VIDEO, TODAY)) == (
        "---\n"
        "video_id: abcdefghijk\n"
        "title: 'A video: part 1'\n"
        "channel: A channel\n"
        "channel_id: UCxxxxxxxxxxxxxxxxxxxxxx\n"
        "upload_date: 2026-10-08\n"
        "duration: 600\n"
        "members_only: false\n"
        "generated: 2026-10-09\n"
        "---\n\n"
    )


def test_render_front_matter_writes_unknown_values_as_null() -> None:
    assert "members_only: null\n" in render_front_matter(GuideMetadata(video_id="abcdefghijk"))


@pytest.mark.parametrize(
    "title",
    ["Quotes \"a\" and 'b'", "yes", "2026", "# not a comment", "--- not a delimiter", "日本語のタイトル " * 20],
)
def test_front_matter_round_trip(title: str) -> None:
    metadata = metadata_from_video(VIDEO.model_copy(update={"title": title}), TODAY)
    text = render_front_matter(metadata) + "# Guide\n"
    assert split_front_matter(text) == (metadata, "# Guide\n")


def test_render_front_matter_keeps_a_long_title_on_one_line() -> None:
    metadata = GuideMetadata(title="word " * 60)
    assert len(render_front_matter(metadata).splitlines()) == len(GuideMetadata.model_fields) + 3


@pytest.mark.parametrize("text", ["# Guide\n\n---\n\nmore\n", "---\nno closing line\n", ""])
def test_split_front_matter_without_a_block(text: str) -> None:
    assert split_front_matter(text) == (GuideMetadata(), text)


def test_split_front_matter_ignores_unknown_keys() -> None:
    metadata, markdown = split_front_matter("---\nvideo_id: abcdefghijk\ntags: [a, b]\n---\n# Guide\n")
    assert metadata == GuideMetadata(video_id="abcdefghijk")
    assert markdown == "# Guide\n"


def test_split_front_matter_empty_block() -> None:
    assert split_front_matter("---\n---\n\n# Guide\n") == (GuideMetadata(), "# Guide\n")


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("---\ntitle: [unclosed\n---\n", "not valid YAML"),
        ("---\njust a sentence\n---\n", "not a set of `key: value` lines"),
        ("---\nmembers_only: maybe\n---\n", "invalid front matter"),
    ],
)
def test_split_front_matter_rejects_a_bad_block(text: str, message: str) -> None:
    with pytest.raises(FrontMatterError, match=message):
        _ = split_front_matter(text)


def test_split_front_matter_reports_the_file_line_of_a_yaml_error() -> None:
    with pytest.raises(FrontMatterError) as info:
        _ = split_front_matter("---\nvideo_id: abcdefghijk\ntitle: a: b\n---\n# Guide\n")
    assert str(info.value) == "the front matter is not valid YAML at line 3: mapping values are not allowed here"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("---\nvideo_id: abcdefghijk\n---\n# Guide\n", True),
        ("---\n---\n# Guide\n", True),
        ("---\nnot: [valid\n---\n", True),
        ("---\r\ntitle: x\r\n---\r\n", True),
        ("---\nno closing line\n", False),
        ("# Guide\n\n---\n\nmore\n---\n", False),
        ("", False),
    ],
)
def test_has_front_matter(text: str, expected: bool) -> None:
    assert has_front_matter(text) is expected
