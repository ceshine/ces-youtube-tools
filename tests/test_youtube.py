import json
import subprocess

import pytest

from youtube_tools.ytdlp import YtDlp, YtDlpError
from youtube_tools.youtube import verify_login, resolve_channel, normalize_channel_url


class FakeRunner:
    """Records commands and returns a canned result."""

    def __init__(self, stdout: str = "", returncode: int = 0, stderr: str = "") -> None:
        self.calls: list[list[str]] = []
        self.result = (stdout, returncode, stderr)

    def __call__(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        stdout, returncode, stderr = self.result
        return subprocess.CompletedProcess(args, returncode, stdout, stderr)


def test_build_args_with_and_without_cookies() -> None:
    assert YtDlp(["yt-dlp"], "firefox:/p").build_args(["URL"]) == [
        "yt-dlp",
        "--cookies-from-browser",
        "firefox:/p",
        "URL",
    ]
    assert YtDlp(["yt-dlp"], None).build_args(["URL"]) == ["yt-dlp", "URL"]


def test_run_raises_with_error_lines() -> None:
    runner = FakeRunner(returncode=1, stderr="WARNING: meh\nERROR: [youtube] bad thing\n")
    with pytest.raises(YtDlpError, match=r"ERROR: \[youtube\] bad thing"):
        _ = YtDlp(["yt-dlp"], None, runner).run(["URL"])


def test_run_json_rejects_non_object() -> None:
    with pytest.raises(YtDlpError, match="expected a JSON object"):
        _ = YtDlp(["yt-dlp"], None, FakeRunner(stdout="null")).run_json(["URL"])


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("@3blue1brown", "https://www.youtube.com/@3blue1brown"),
        ("https://www.youtube.com/@3blue1brown/videos", "https://www.youtube.com/@3blue1brown"),
        ("youtube.com/@a.b-c", "https://www.youtube.com/@a.b-c"),
        (
            "https://m.youtube.com/channel/UCYO_jab_esuFRV4b17AJtAw/membership",
            "https://www.youtube.com/channel/UCYO_jab_esuFRV4b17AJtAw",
        ),
    ],
)
def test_normalize_channel_url(value: str, expected: str) -> None:
    assert normalize_channel_url(value) == expected


@pytest.mark.parametrize("value", ["3blue1brown", "https://www.youtube.com/watch?v=abc", "https://example.com/@a"])
def test_normalize_channel_url_rejects(value: str) -> None:
    with pytest.raises(ValueError, match="Not a YouTube channel"):
        _ = normalize_channel_url(value)


def test_resolve_channel() -> None:
    runner = FakeRunner(stdout=json.dumps({"channel": "3Blue1Brown", "channel_id": "UCYO_jab_esuFRV4b17AJtAw"}))
    channel = resolve_channel("@3blue1brown", YtDlp(["yt-dlp"], None, runner))
    assert channel.name == "3Blue1Brown"
    assert channel.channel_id == "UCYO_jab_esuFRV4b17AJtAw"
    assert channel.url == "https://www.youtube.com/@3blue1brown"
    assert not channel.members
    assert runner.calls[0][-1] == "https://www.youtube.com/@3blue1brown"


def test_resolve_channel_without_id() -> None:
    with pytest.raises(YtDlpError, match="no channel ID"):
        _ = resolve_channel("@x", YtDlp(["yt-dlp"], None, FakeRunner(stdout="{}")))


def test_verify_login() -> None:
    assert verify_login(YtDlp(["yt-dlp"], "firefox:/p", FakeRunner(stdout="abc\n")))
    assert not verify_login(YtDlp(["yt-dlp"], "firefox:/p", FakeRunner(returncode=1, stderr="ERROR: login")))
