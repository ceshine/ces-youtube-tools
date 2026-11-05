"""Guide generator that runs the `claude` CLI (Claude Code) in print mode."""

import os
import subprocess
from pathlib import Path
from typing import Literal

from pydantic import Field, BaseModel, ConfigDict, field_validator

from .base import SYSTEM_PROMPT, refuse_recorded_flags
from ._subprocess import PromptRunner, run_prompt_command
from ..guide import GuideError
from ..video import VideoInfo

GENERATOR_NAME = "claude-cli"
HARNESS = "Claude Code"

DISABLE_CLAUDE_MDS_ENV = "CLAUDE_CODE_DISABLE_CLAUDE_MDS"
DISABLE_PROMPT_CACHING_ENV = "DISABLE_PROMPT_CACHING"

EffortLevel = Literal["low", "medium", "high", "xhigh", "max"]

DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_EFFORT: EffortLevel = "high"
EXTRA_ARGS_HINTS = {"--model": "set the model option instead", "--effort": "set the effort option instead"}


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
        model (str): Model alias or ID, always passed as `--model`, so it overrides Claude Code's own model setting.
        effort (EffortLevel): Effort level (`low`, `medium`, `high`, `xhigh` or `max`), always passed as `--effort`.
        bare (bool): Pass `--bare`, which skips hooks, plugins and CLAUDE.md discovery but only works with an
            `ANTHROPIC_API_KEY` (not with a Claude subscription login).
        prompt_caching (bool): Allow Claude Code's prompt caching. Off by default: each run is one request that is
            never repeated, so a cache write only adds cost.
        timeout_seconds (float): How long one generation may take.
        session_dir (Path | None): Folder to run `claude` in with session logging on, so every log lands in one
            folder under `~/.claude/projects/`. None passes `--no-session-persistence` and keeps no log.
        extra_args (list[str]): More arguments, added last. They cannot hold `--model` or `--effort`, which the
            `model` and `effort` options set.
    """

    model_config = ConfigDict(extra="forbid")

    command: list[str] = Field(default=["claude"], description="Command that runs Claude Code.")
    model: str = Field(
        default=DEFAULT_MODEL,
        min_length=1,
        description="Model alias or ID, always passed as --model (overrides Claude Code's own model setting).",
    )
    effort: EffortLevel = Field(default=DEFAULT_EFFORT, description="Effort level, always passed as --effort.")
    bare: bool = Field(
        default=False,
        description="Pass --bare (skips hooks, plugins and CLAUDE.md). Needs an ANTHROPIC_API_KEY, not a subscription.",
    )
    prompt_caching: bool = Field(
        default=False, description="Allow prompt caching. Off by default: each run is a single request."
    )
    timeout_seconds: float = Field(default=1800, description="How long one generation may take.")
    session_dir: Path | None = Field(
        default=None, description="Run claude here with session logging on. Unset keeps no session log."
    )
    extra_args: list[str] = Field(
        default=[], description="More arguments for claude, added last; not --model or --effort."
    )

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

    @field_validator("extra_args")
    @classmethod
    def check_extra_args(cls, value: list[str]) -> list[str]:
        """Refuse `--model` and `--effort` in `extra_args`, alone or as `--model=...`.

        Args:
            value (list[str]): The configured arguments.

        Returns:
            list[str]: The arguments, unchanged.

        Raises:
            ValueError: If an argument is `--model` or `--effort`, with or without `=...`.
        """
        return refuse_recorded_flags(value, EXTRA_ARGS_HINTS)


class ClaudeCliGenerator:
    """Generates a guide with `claude -p`, with all tools disabled.

    CLAUDE.md files are disabled and a short system prompt replaces Claude Code's coding-agent prompt. Without
    `session_dir` the process runs in the video's work directory and keeps no session log. With it, the process runs
    in `session_dir` and each logged session is named after the video ID.

    Args:
        options (ClaudeCliOptions): Settings.
        runner (PromptRunner): Function that runs the command. Replace it in tests.
    """

    def __init__(self, options: ClaudeCliOptions, runner: PromptRunner = run_claude) -> None:
        self.options: ClaudeCliOptions = options
        self.runner: PromptRunner = runner

    @property
    def harness(self) -> str:
        """str: `Claude Code`."""
        return HARNESS

    @property
    def model(self) -> str:
        """str: The `model` option, passed as `--model`."""
        return self.options.model

    @property
    def effort(self) -> str:
        """str: The `effort` option, passed as `--effort`."""
        return self.options.effort

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
        args += ["--model", self.options.model, "--effort", self.options.effort]
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
        return run_prompt_command(
            self.runner,
            args,
            prompt,
            cwd,
            self.options.timeout_seconds,
            extra_env,
            display_name="claude",
            install_hint="Is Claude Code installed and on PATH?",
            options_table=f"guide.options.{GENERATOR_NAME}",
        )
