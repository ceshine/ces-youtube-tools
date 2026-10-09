"""Run yt-dlp as a subprocess."""

import json
import logging
import subprocess
from collections.abc import Callable
from typing import Any

LOGGER = logging.getLogger(__name__)

Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]


class YtDlpError(RuntimeError):
    """Raised when yt-dlp fails or returns unusable output."""


def run_subprocess(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a command and capture its text output.

    Args:
        args (list[str]): The command and its arguments.

    Returns:
        subprocess.CompletedProcess[str]: The finished process.
    """
    return subprocess.run(args, capture_output=True, text=True, check=False)


class YtDlp:
    """Builds and runs yt-dlp commands with a fixed cookie source.

    Args:
        command (list[str]): The command that runs yt-dlp, e.g. `["yt-dlp"]`.
        cookies_spec (str | None): Value for `--cookies-from-browser`, or None to run without cookies.
        runner (Runner): Function that runs a command. Replace it in tests.
    """

    def __init__(self, command: list[str], cookies_spec: str | None, runner: Runner = run_subprocess) -> None:
        self.command: list[str] = command
        self.cookies_spec: str | None = cookies_spec
        self.runner: Runner = runner

    def build_args(self, args: list[str]) -> list[str]:
        """Build the full command line.

        Args:
            args (list[str]): yt-dlp arguments.

        Returns:
            list[str]: The command, the cookie options and `args`.
        """
        cookie_args = ["--cookies-from-browser", self.cookies_spec] if self.cookies_spec else []
        return [*self.command, *cookie_args, *args]

    def run(self, args: list[str]) -> str:
        """Run yt-dlp and return its standard output.

        Args:
            args (list[str]): yt-dlp arguments.

        Returns:
            str: Standard output.

        Raises:
            YtDlpError: If yt-dlp exits with a non-zero status.
        """
        full_args = self.build_args(args)
        LOGGER.debug("Running %s", full_args)
        result = self.runner(full_args)
        if result.returncode != 0:
            errors = [line for line in result.stderr.splitlines() if line.startswith("ERROR")]
            detail = "\n".join(errors) or result.stderr.strip()[-1000:]
            raise YtDlpError(f"yt-dlp exited with status {result.returncode}: {detail}")
        return result.stdout

    def run_json(self, args: list[str]) -> dict[str, Any]:
        """Run yt-dlp with `-J` and parse the JSON it prints.

        Args:
            args (list[str]): yt-dlp arguments, without `-J`.

        Returns:
            dict[str, Any]: The parsed JSON object.

        Raises:
            YtDlpError: If yt-dlp fails or does not print a JSON object.
        """
        output = self.run(["-J", *args])
        try:
            data = json.loads(output)
        except json.JSONDecodeError as exc:
            raise YtDlpError(f"yt-dlp did not print valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise YtDlpError(f"yt-dlp printed {type(data).__name__}, expected a JSON object")
        return data
