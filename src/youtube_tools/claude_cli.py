"""Guide generator that runs the `claude` CLI (Claude Code) in print mode."""

import os
import logging
import subprocess
from pathlib import Path
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, field_validator

from .guide import GuideError
from .video import VideoInfo

LOGGER = logging.getLogger(__name__)

GENERATOR_NAME = "claude-cli"
SYSTEM_PROMPT = (
    "You write watching guides for videos from their transcripts, following the user's format instructions exactly. "
    "Reply with the Markdown guide only."
)

DISABLE_CLAUDE_MDS_ENV = "CLAUDE_CODE_DISABLE_CLAUDE_MDS"
DISABLE_PROMPT_CACHING_ENV = "DISABLE_PROMPT_CACHING"

ClaudeRunner = Callable[[list[str], str, Path, float, dict[str, str]], subprocess.CompletedProcess[str]]


def run_claude(
    args: list[str], prompt: str, cwd: Path, timeout: float, extra_env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """Run the `claude` CLI with the prompt on stdin and CLAUDE.md files disabled.

    Args:
        args (list[str]): The command and its arguments.
        prompt (str): Text sent to stdin.
        cwd (Path): Working directory for the process.
        timeout (float): Seconds to wait before giving up.
        extra_env (dict[str, str]): Environment variables added on top of the current environment.

    Returns:
        subprocess.CompletedProcess[str]: The finished process.

    Raises:
        subprocess.TimeoutExpired: If the process runs longer than `timeout`.
    """
    env = {**os.environ, DISABLE_CLAUDE_MDS_ENV: "1", **extra_env}
    return subprocess.run(
        args, input=prompt, capture_output=True, text=True, cwd=cwd, timeout=timeout, env=env, check=False
    )


class ClaudeCliOptions(BaseModel):
    """Settings from the `[guide.options.claude-cli]` table.

    Attributes:
        command (list[str]): Command that runs Claude Code.
        model (str | None): Model alias or ID for `--model`. None uses Claude Code's default.
        effort (str | None): Effort level for `--effort` (`low` to `max`). None uses the default.
        bare (bool): Pass `--bare`, which skips hooks, plugins and CLAUDE.md discovery but only works with an
            `ANTHROPIC_API_KEY` (not with a Claude subscription login).
        prompt_caching (bool): Allow Claude Code's prompt caching. Off by default: each run is one request that is
            never repeated, so a cache write only adds cost.
        timeout_seconds (float): How long one generation may take.
        session_dir (Path | None): Folder to run `claude` in with session logging on, so every log lands in one
            folder under `~/.claude/projects/`. None passes `--no-session-persistence` and keeps no log.
        extra_args (list[str]): More arguments, added last.
    """

    model_config = ConfigDict(extra="forbid")

    command: list[str] = ["claude"]
    model: str | None = None
    effort: str | None = None
    bare: bool = False
    prompt_caching: bool = False
    timeout_seconds: float = 1800
    session_dir: Path | None = None
    extra_args: list[str] = []

    @field_validator("session_dir")
    @classmethod
    def expand_session_dir(cls, value: Path | None) -> Path | None:
        """Expand a leading `~` in `session_dir`.

        Args:
            value (Path | None): The configured folder.

        Returns:
            Path | None: The expanded folder, or None when it is not set.
        """
        return value.expanduser() if value is not None else None


class ClaudeCliGenerator:
    """Generates a guide with `claude -p`, with all tools disabled.

    CLAUDE.md files are disabled and a short system prompt replaces Claude Code's coding-agent prompt. Without
    `session_dir` the process runs in the video's work directory and keeps no session log. With it, the process runs
    in `session_dir` and each logged session is named after the video ID.

    Args:
        options (ClaudeCliOptions): Settings.
        runner (ClaudeRunner): Function that runs the command. Replace it in tests.
    """

    def __init__(self, options: ClaudeCliOptions, runner: ClaudeRunner = run_claude) -> None:
        self.options: ClaudeCliOptions = options
        self.runner: ClaudeRunner = runner

    def build_args(self, video_id: str | None = None) -> list[str]:
        """Build the command line.

        Args:
            video_id (str | None): Name for the logged session. Only used when `session_dir` is set.

        Returns:
            list[str]: The command and its arguments.
        """
        args = [
            *self.options.command,
            "--print",
            "--tools",
            "",
            "--output-format",
            "text",
            "--system-prompt",
            SYSTEM_PROMPT,
        ]
        if self.options.session_dir is None:
            args.append("--no-session-persistence")
        elif video_id:
            args += ["--name", video_id]
        if self.options.model:
            args += ["--model", self.options.model]
        if self.options.effort:
            args += ["--effort", self.options.effort]
        if self.options.bare:
            args.append("--bare")
        return [*args, *self.options.extra_args]

    def generate(self, prompt: str, video: VideoInfo, work_dir: Path) -> str:
        """Run Claude Code on the prompt and return its reply.

        Args:
            prompt (str): The full prompt.
            video (VideoInfo): The video. Its ID names the logged session.
            work_dir (Path): The video's work directory. It is the working directory unless `session_dir` is set.

        Returns:
            str: The reply.

        Raises:
            GuideError: If `session_dir` is not a folder, or the command is missing, times out, fails or prints
                nothing.
        """
        args = self.build_args(video.id)
        cwd = work_dir
        if self.options.session_dir is not None:
            cwd = self.options.session_dir
            try:
                cwd.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise GuideError(f"Cannot use session_dir {cwd}: {exc}") from exc
        extra_env = {} if self.options.prompt_caching else {DISABLE_PROMPT_CACHING_ENV: "1"}
        LOGGER.debug("Running %s in %s with a %d-character prompt", args[0], cwd, len(prompt))
        try:
            result = self.runner(args, prompt, cwd, self.options.timeout_seconds, extra_env)
        except FileNotFoundError as exc:
            raise GuideError(f"Cannot run {args[0]!r}. Is Claude Code installed and on PATH?") from exc
        except subprocess.TimeoutExpired as exc:
            raise GuideError(
                f"claude did not finish within {self.options.timeout_seconds:g} seconds. Raise timeout_seconds in "
                f"[guide.options.{GENERATOR_NAME}]."
            ) from exc
        if result.returncode != 0:
            detail = (result.stderr.strip() or result.stdout.strip())[-1000:]
            raise GuideError(f"claude exited with status {result.returncode}: {detail}")
        if not result.stdout.strip():
            raise GuideError("claude returned an empty reply.")
        return result.stdout
