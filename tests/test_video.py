import pytest

from ces_youtube_tools.video import Chapter, parse_video_id, video_info_from_ytdlp


@pytest.mark.parametrize(
    "value",
    [
        "3i2C3IB3PaA",
        "https://www.youtube.com/watch?v=3i2C3IB3PaA",
        "https://www.youtube.com/watch?v=3i2C3IB3PaA&t=67s",
        "https://www.youtube.com/watch?feature=share&v=3i2C3IB3PaA",
        "youtube.com/watch?v=3i2C3IB3PaA",
        "https://m.youtube.com/watch?v=3i2C3IB3PaA",
        "https://youtu.be/3i2C3IB3PaA",
        "https://youtu.be/3i2C3IB3PaA?si=abc",
        "https://www.youtube.com/shorts/3i2C3IB3PaA",
        "https://www.youtube.com/live/3i2C3IB3PaA?feature=shared",
        "https://www.youtube.com/embed/3i2C3IB3PaA",
        "  3i2C3IB3PaA  ",
    ],
)
def test_parse_video_id(value: str) -> None:
    assert parse_video_id(value) == "3i2C3IB3PaA"


@pytest.mark.parametrize(
    "value", ["", "short", "https://example.com/watch?v=3i2C3IB3PaA", "https://www.youtube.com/@channel"]
)
def test_parse_video_id_rejects(value: str) -> None:
    with pytest.raises(ValueError, match="Not a YouTube video"):
        _ = parse_video_id(value)


def test_video_info_from_ytdlp() -> None:
    info = video_info_from_ytdlp(
        {
            "id": "GlYgs6v2YfU",
            "title": "Cross-entropy",
            "channel": "3Blue1Brown",
            "channel_id": "UCYO_jab_esuFRV4b17AJtAw",
            "duration": 2031,
            "upload_date": "20260716",
            "description": None,
            "language": "en",
            "availability": "public",
            "chapters": [{"start_time": 0.0, "end_time": 182.0, "title": "Intro"}],
            "subtitles": {"en": [], "live_chat": [], "es": []},
            "automatic_captions": {"en-orig": [], "en": []},
            "formats": [{"url": "https://signed.example/very-long"}],
        }
    )
    assert info.title == "Cross-entropy"
    assert info.description == ""
    assert info.chapters == [Chapter(start=0.0, end=182.0, title="Intro")]
    assert info.subtitle_languages == ["en", "es"]  # live chat is not a subtitle track
    assert info.auto_caption_languages == ["en-orig", "en"]
    assert info.url == "https://www.youtube.com/watch?v=GlYgs6v2YfU"
    assert "signed.example" not in info.model_dump_json()


def test_video_info_from_ytdlp_minimal() -> None:
    info = video_info_from_ytdlp({"id": "abcdefghijk"})
    assert info.title == "abcdefghijk"
    assert info.chapters == []
    assert info.subtitle_languages == []
