"""Guide generator that runs the `claude` CLI (Claude Code) in print mode."""

import logging
import subprocess
from pathlib import Path
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict

from .guide import GuideError

LOGGER = logging.getLogger(__name__)

GENERATOR_NAME = "claude-cli"
SYSTEM_PROMPT = (
    "You write watching guides for videos from their transcripts, following the user's format instructions exactly. "
    "Reply with the Markdown guide only."
)

ClaudeRunner = Callable[[list[str], str, Path, float], subprocess.CompletedProcess[str]]


def run_claude(args: list[str], prompt: str, cwd: Path, timeout: float) -> subprocess.CompletedProcess[str]:
    """Run the `claude` CLI with the prompt on stdin.

    Args:
        args (list[str]): The command and its arguments.
        prompt (str): Text sent to stdin.
        cwd (Path): Working directory for the process.
        timeout (float): Seconds to wait before giving up.

    Returns:
        subprocess.CompletedProcess[str]: The finished process.

    Raises:
        subprocess.TimeoutExpired: If the process runs longer than `timeout`.
    """
    return subprocess.run(args, input=prompt, capture_output=True, text=True, cwd=cwd, timeout=timeout, check=False)


class ClaudeCliOptions(BaseModel):
    """Settings from the `[guide.options.claude-cli]` table.

    Attributes:
        command (list[str]): Command that runs Claude Code.
        model (str | None): Model alias or ID for `--model`. None uses Claude Code's default.
        effort (str | None): Effort level for `--effort` (`low` to `max`). None uses the default.
        bare (bool): Pass `--bare`, which skips hooks, plugins and CLAUDE.md discovery but only works with an
            `ANTHROPIC_API_KEY` (not with a Claude subscription login).
        timeout_seconds (float): How long one generation may take.
        extra_args (list[str]): More arguments, added last.
    """

    model_config = ConfigDict(extra="forbid")

    command: list[str] = ["claude"]
    model: str | None = None
    effort: str | None = None
    bare: bool = False
    timeout_seconds: float = 1800
    extra_args: list[str] = []


class ClaudeCliGenerator:
    """Generates a guide with `claude -p`, with all tools disabled.

    The process runs in the video's work directory, so no project CLAUDE.md or AGENTS.md is picked up, and a short
    system prompt replaces Claude Code's coding-agent prompt.

    Args:
        options (ClaudeCliOptions): Settings.
        runner (ClaudeRunner): Function that runs the command. Replace it in tests.
    """

    def __init__(self, options: ClaudeCliOptions, runner: ClaudeRunner = run_claude) -> None:
        self.options: ClaudeCliOptions = options
        self.runner: ClaudeRunner = runner

    def build_args(self) -> list[str]:
        """Build the command line.

        Returns:
            list[str]: The command and its arguments.
        """
        args = [
            *self.options.command,
            "--print",
            "--tools",
            "",
            "--no-session-persistence",
            "--output-format",
            "text",
            "--system-prompt",
            SYSTEM_PROMPT,
        ]
        if self.options.model:
            args += ["--model", self.options.model]
        if self.options.effort:
            args += ["--effort", self.options.effort]
        if self.options.bare:
            args.append("--bare")
        return [*args, *self.options.extra_args]

    def generate(self, prompt: str, work_dir: Path) -> str:
        """Run Claude Code on the prompt and return its reply.

        Args:
            prompt (str): The full prompt.
            work_dir (Path): The video's work directory, used as the working directory.

        Returns:
            str: The reply.

        Raises:
            GuideError: If the command is missing, times out, fails or prints nothing.
        """
        args = self.build_args()
        LOGGER.debug("Running %s with a %d-character prompt", args[0], len(prompt))
        try:
            result = self.runner(args, prompt, work_dir, self.options.timeout_seconds)
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
