"""Run a guide generator's command with the prompt on stdin, and turn its failures into `GuideError`."""

import logging
import subprocess
from pathlib import Path
from collections.abc import Callable

from ..guide import GuideError

LOGGER = logging.getLogger(__name__)

PromptRunner = Callable[[list[str], str, Path, float, dict[str, str]], subprocess.CompletedProcess[str]]


def run_prompt_command(
    runner: PromptRunner,
    args: list[str],
    prompt: str,
    cwd: Path,
    timeout: float,
    extra_env: dict[str, str],
    *,
    display_name: str,
    install_hint: str,
    options_table: str,
) -> str:
    """Run a command on the prompt and return what it printed.

    Args:
        runner (PromptRunner): Function that runs the command, e.g. with `subprocess.run`. Replace it in tests.
        args (list[str]): The command and its arguments.
        prompt (str): Text sent to stdin.
        cwd (Path): Working directory for the process.
        timeout (float): Seconds to wait before giving up.
        extra_env (dict[str, str]): Environment variables added on top of the current environment.
        display_name (str): The tool's name in the error messages, e.g. "claude".
        install_hint (str): Sentence added when the command cannot be found, e.g. "Is Claude Code installed and on
            PATH?".
        options_table (str): The config table that holds `timeout_seconds`, e.g. "guide.options.claude-cli".

    Returns:
        str: The command's standard output.

    Raises:
        GuideError: If the command is missing, times out, exits with a non-zero status or prints nothing.
    """
    LOGGER.debug("Running %s in %s with a %d-character prompt", args[0], cwd, len(prompt))
    try:
        result = runner(args, prompt, cwd, timeout, extra_env)
    except FileNotFoundError as exc:
        raise GuideError(f"Cannot run {args[0]!r}. {install_hint}") from exc
    except subprocess.TimeoutExpired as exc:
        raise GuideError(
            f"{display_name} did not finish within {timeout:g} seconds. Raise timeout_seconds in [{options_table}]."
        ) from exc
    if result.returncode != 0:
        detail = (result.stderr.strip() or result.stdout.strip())[-1000:]
        raise GuideError(f"{display_name} exited with status {result.returncode}: {detail}")
    if not result.stdout.strip():
        raise GuideError(f"{display_name} returned an empty reply.")
    return result.stdout
