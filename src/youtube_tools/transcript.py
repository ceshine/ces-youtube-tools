"""Transcript models and the interface every transcript provider implements."""

from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from .video import VideoInfo


class TranscriptError(Exception):
    """Raised when a provider cannot produce a transcript for a video."""


class TranscriptSegment(BaseModel):
    """A piece of speech with its timing.

    Attributes:
        start (float): Start time in seconds.
        end (float): End time in seconds.
        text (str): What was said.
    """

    start: float
    end: float
    text: str


class Transcript(BaseModel):
    """A video's transcript, independent of how it was produced.

    Attributes:
        video_id (str): YouTube video ID.
        source (str): How the transcript was made, e.g. `yt-dlp-subs: automatic en-orig`.
        language (str): Language code of the transcript.
        segments (list[TranscriptSegment]): Segments in time order.
    """

    video_id: str
    source: str
    language: str
    segments: list[TranscriptSegment]


class TranscriptProvider(Protocol):
    """Produces a transcript for a video, e.g. from subtitles or by transcribing the audio."""

    def fetch(self, video: VideoInfo, work_dir: Path) -> Transcript:
        """Produce the transcript.

        Args:
            video (VideoInfo): The video.
            work_dir (Path): The video's work directory, for intermediate files such as subtitle downloads.

        Returns:
            Transcript: The transcript.

        Raises:
            TranscriptError: If no transcript can be produced.
        """
        ...
