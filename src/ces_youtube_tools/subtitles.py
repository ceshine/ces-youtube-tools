"""Transcript provider that downloads a video's subtitles with yt-dlp."""

from pathlib import Path
from dataclasses import dataclass

from .vtt import parse_vtt
from .video import VideoInfo
from .ytdlp import YtDlp
from .transcript import Transcript, TranscriptError

PROVIDER_NAME = "yt-dlp-subs"


@dataclass(frozen=True)
class SubtitleTrack:
    """A subtitle track to download.

    Attributes:
        language (str): Track language code, as yt-dlp lists it (e.g. `en`, `en-US`, `en-orig`).
        automatic (bool): True for YouTube's automatic captions, False for human-made subtitles.
    """

    language: str
    automatic: bool


def choose_track(video: VideoInfo, languages: list[str]) -> SubtitleTrack | None:
    """Pick the subtitle track to use.

    Human-made subtitles come first, then automatic captions, each in the order of `languages`. A human-made track
    matches a language exactly or by region (`en` matches `en-US`). Automatic captions must match exactly, because
    their codes distinguish the original captions (`en-orig`) from machine translations.

    Args:
        video (VideoInfo): The video, with its available tracks.
        languages (list[str]): Preferred language codes, most preferred first.

    Returns:
        SubtitleTrack | None: The chosen track, or None if no track matches.
    """
    for wanted in languages:
        if wanted in video.subtitle_languages:
            return SubtitleTrack(language=wanted, automatic=False)
        regional = next((language for language in video.subtitle_languages if language.startswith(f"{wanted}-")), None)
        if regional is not None:
            return SubtitleTrack(language=regional, automatic=False)
    for wanted in languages:
        if wanted in video.auto_caption_languages:
            return SubtitleTrack(language=wanted, automatic=True)
    return None


class YtDlpSubtitleProvider:
    """Downloads the best matching subtitle track and parses it.

    Args:
        ytdlp (YtDlp): The yt-dlp runner. It needs cookies for members-only videos.
        languages (list[str]): Preferred language codes, most preferred first.
    """

    def __init__(self, ytdlp: YtDlp, languages: list[str]) -> None:
        self.ytdlp: YtDlp = ytdlp
        self.languages: list[str] = languages

    def fetch(self, video: VideoInfo, work_dir: Path) -> Transcript:
        """Download the chosen subtitle track into `work_dir/subtitles` and parse it.

        Args:
            video (VideoInfo): The video.
            work_dir (Path): The video's work directory.

        Returns:
            Transcript: The transcript.

        Raises:
            TranscriptError: If no track matches the preferred languages, the subtitle directory cannot be created,
                or the download produced no file.
            YtDlpError: If yt-dlp fails.
        """
        track = choose_track(video, self.languages)
        if track is None:
            raise TranscriptError(
                f"No subtitles in {', '.join(self.languages)} for {video.id}. "
                f"Human-made: {', '.join(video.subtitle_languages) or 'none'}. "
                f"Automatic: {', '.join(video.auto_caption_languages[:10]) or 'none'}"
                f"{' ...' if len(video.auto_caption_languages) > 10 else ''}."
            )
        subtitle_dir = work_dir / "subtitles"
        try:
            subtitle_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise TranscriptError(f"Cannot create the subtitle directory {subtitle_dir} ({exc}).") from exc
        _ = self.ytdlp.run(
            [
                "--skip-download",
                "--write-auto-subs" if track.automatic else "--write-subs",
                "--sub-langs",
                track.language,
                "--sub-format",
                "vtt",
                "--paths",
                str(subtitle_dir),
                "--output",
                "%(id)s.%(ext)s",
                video.url,
            ]
        )
        subtitle_file = subtitle_dir / f"{video.id}.{track.language}.vtt"
        if not subtitle_file.exists():
            raise TranscriptError(f"yt-dlp did not write {subtitle_file}. The track may not be available as WebVTT.")
        kind = "automatic" if track.automatic else "human-made"
        return Transcript(
            video_id=video.id,
            source=f"{PROVIDER_NAME}: {kind} {track.language}",
            language=track.language,
            segments=parse_vtt(subtitle_file.read_text(encoding="utf-8")),
        )
