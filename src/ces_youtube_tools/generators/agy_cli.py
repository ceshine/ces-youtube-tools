"""Guide generator that runs the `agy` CLI (Antigravity CLI) in print mode, with stream-json input and output."""

import os
import re
import json
import time
import logging
import tempfile
import threading
import contextlib
import subprocess
from pathlib import Path
from typing import IO
from collections.abc import Callable

from pydantic import Field, BaseModel, ConfigDict, ValidationError, field_validator

from .base import SYSTEM_PROMPT, refuse_recorded_flags
from ._subprocess import PromptRunner, run_prompt_command
from ..guide import GuideError
from ..video import VideoInfo

LOGGER = logging.getLogger(__name__)

GENERATOR_NAME = "agy-cli"
HARNESS = "Antigravity CLI"
AGENT_NAME = "ces-guide-writer"
DEFAULT_MODEL = "gemini-3.8-flash-medium"
# A Gemini model ID that ends in its effort level, one of the levels `agy --help` lists for `--effort`. A family name
# such as `gemini-3.8-flash` also works in `agy`, but picks a level (High in 1.5.1) that the front matter would not
# record. Claude models refuse `--effort`, and other model families were not checked.
MODEL_PATTERN = re.compile(r"gemini(?:-[a-z0-9.]+)+?-(?:low|medium|high|xhigh|max)")
EXTRA_ARGS_HINTS = {
    "--model": "set the model option instead",
    "--effort": "choose a model ID that names the effort level, e.g. gemini-3.8-flash-high",
}
# `agy` 1.5.1 sometimes finishes its turn, prints the `result` event and then hangs while shutting down, ignoring
# SIGTERM too. After the `result` event it gets this long to exit, then SIGTERM, then SIGKILL after the second wait.
EXIT_GRACE_SECONDS = 15.0
# Long enough for a slow but real shutdown, such as a migration of agy's own storage, to finish before SIGKILL.
KILL_GRACE_SECONDS = 30.0


def run_agy(
    args: list[str],
    prompt: str,
    cwd: Path,
    timeout: float,
    extra_env: dict[str, str],
    *,
    exit_grace: float = EXIT_GRACE_SECONDS,
    kill_grace: float = KILL_GRACE_SECONDS,
) -> subprocess.CompletedProcess[str]:
    """Run the `agy` CLI with the prompt on stdin, and stop it if it does not exit after printing its result.

    Standard output is read line by line. Once a `result` event arrives, `agy` has `exit_grace` seconds to exit by
    itself. If it does not, it is sent SIGTERM, then SIGKILL after `kill_grace` more seconds, and the run counts as
    finished: the return code is 0, since the reply is complete. The pipes use UTF-8, and bytes that are not UTF-8
    become U+FFFD.

    Args:
        args (list[str]): The command and its arguments.
        prompt (str): Text sent to stdin.
        cwd (Path): Working directory for the process.
        timeout (float): Seconds to wait for the `result` event, or for the process to exit without one.
        extra_env (dict[str, str]): Environment variables added on top of the current environment.
        exit_grace (float): Seconds `agy` has to exit by itself after the `result` event.
        kill_grace (float): Seconds between SIGTERM and SIGKILL.

    Returns:
        subprocess.CompletedProcess[str]: The finished process, with everything it printed.

    Raises:
        subprocess.TimeoutExpired: If neither a `result` event nor the process's exit comes within `timeout`. The
            process is killed first.
        Exception: Whatever a thread that writes the prompt or reads the output raised, at once. The process is killed
            first.
    """
    deadline = time.monotonic() + timeout
    env = {**os.environ, **extra_env}
    process = subprocess.Popen(
        args,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
        cwd=cwd,
        env=env,
    )
    # typeshed types the pipes of a text-mode process as IO[Any].
    stdin: IO[str] | None = process.stdin
    stdout: IO[str] | None = process.stdout
    stderr: IO[str] | None = process.stderr
    if stdin is None or stdout is None or stderr is None:
        process.kill()
        raise RuntimeError("Popen did not open the stdin, stdout and stderr pipes it was given.")
    stdout_lines: list[str] = []
    stderr_parts: list[str] = []
    thread_errors: list[Exception] = []
    result_seen = threading.Event()
    stop_waiting = threading.Event()  # Set on the result event, when stdout closes, or when a thread fails.

    def write_prompt() -> None:
        # As in `Popen.communicate`: if agy exits before reading the prompt, its exit status and stderr say why.
        with contextlib.suppress(BrokenPipeError):
            _ = stdin.write(prompt)
        with contextlib.suppress(BrokenPipeError):
            stdin.close()

    def read_stdout() -> None:
        try:
            for line in stdout:
                stdout_lines.append(line)
                if is_result_event(line):
                    result_seen.set()
                    stop_waiting.set()
        finally:
            stop_waiting.set()

    def read_stderr() -> None:
        stderr_parts.append(stderr.read())

    def run_guarded(target: Callable[[], None]) -> None:
        # An exception in a thread never reaches the caller by itself, so any exception is kept and raised below.
        try:
            target()
        except Exception as exc:  # noqa: BLE001 - re-raised in the calling thread.
            thread_errors.append(exc)
            stop_waiting.set()

    threads = [
        threading.Thread(target=run_guarded, args=(target,), daemon=True)
        for target in (write_prompt, read_stdout, read_stderr)
    ]
    for thread in threads:
        thread.start()
    try:
        if not stop_waiting.wait(timeout):
            raise subprocess.TimeoutExpired(args, timeout)
        if thread_errors:
            raise thread_errors[0]
        if result_seen.is_set():
            returncode = stop_after_result(process, exit_grace, kill_grace)
        else:
            # stdout closed without a result: agy has exited, or is about to.
            returncode = process.wait(max(0.0, deadline - time.monotonic()))
    finally:
        if process.poll() is None:
            process.kill()
            _ = process.wait()
        for thread, pipe in zip(threads, (stdin, stdout, stderr), strict=True):
            # The pipes close when agy dies. The limit only matters if a process agy started still holds them; a
            # pipe whose thread is still reading stays open.
            thread.join(kill_grace)
            if not thread.is_alive():
                with contextlib.suppress(OSError):
                    pipe.close()
    if thread_errors:
        raise thread_errors[0]
    return subprocess.CompletedProcess(args, returncode, "".join(stdout_lines), "".join(stderr_parts))


def stop_after_result(process: subprocess.Popen[str], exit_grace: float, kill_grace: float) -> int:
    """Wait for `agy` to exit after its `result` event, and stop it if it does not.

    Args:
        process (subprocess.Popen[str]): The running `agy`.
        exit_grace (float): Seconds it has to exit by itself.
        kill_grace (float): Seconds between SIGTERM and SIGKILL.

    Returns:
        int: Its exit status if it exited by itself, otherwise 0: the reply is complete.
    """
    try:
        return process.wait(exit_grace)
    except subprocess.TimeoutExpired:
        pass
    LOGGER.warning("agy did not exit within %g seconds after printing its result; stopping it.", exit_grace)
    process.terminate()
    try:
        _ = process.wait(kill_grace)
    except subprocess.TimeoutExpired:
        LOGGER.warning("agy ignored SIGTERM for %g seconds; killing it.", kill_grace)
        process.kill()
        _ = process.wait()
    return 0


def agent_file_text() -> str:
    """Build the custom agent file that turns off every tool and sets the system prompt.

    Returns:
        str: The agent file's Markdown, front matter included.
    """
    return (
        "---\n"
        f"name: {AGENT_NAME}\n"
        "description: Writes watching guides from video transcripts.\n"
        "mainAgent: true\n"
        "tools: []\n"
        "excludeDefaultComponents: true\n"
        "---\n"
        "# Guide writer\n"
        "\n"
        f"{SYSTEM_PROMPT}\n"
    )


def stream_json_message(prompt: str) -> str:
    """Encode the prompt as the one stream-json line `agy` reads from stdin.

    Args:
        prompt (str): The full prompt.

    Returns:
        str: The JSON message, ending in a newline. The prompt's own newlines are escaped, so it is one line.
    """
    return json.dumps({"event": "user", "message": {"content": prompt}}) + "\n"


class AgyResult(BaseModel):
    """The `result` of `agy`'s final stream-json event. Other fields are ignored.

    Attributes:
        status (str): `SUCCESS` or `ERROR`.
        response (str): The reply.
        error (str | None): Why the run failed, when it did.
        finished_reason (str | None): Why the turn ended early (a timeout, an interruption, an error). Absent when the
            turn finished normally.
    """

    status: str
    response: str = ""
    error: str | None = None
    finished_reason: str | None = None


class AgyEvent(BaseModel):
    """One line of `agy`'s stream-json output. Only `result` events are read, so other events may have any shape.

    Attributes:
        event (str | None): The event type, such as `init`, `step_update` or `result`.
        result (object): The outcome, on a `result` event. Checked against `AgyResult` only there.
    """

    event: str | None = None
    result: object = None


def is_result_event(line: str) -> bool:
    """Tell whether a line of `agy`'s output is a `result` event, so the run can stop waiting.

    Args:
        line (str): One line of standard output.

    Returns:
        bool: True if the line is a JSON object whose `event` is `result`. `parse_reply` checks the event itself.
    """
    try:
        return AgyEvent.model_validate_json(line).event == "result"
    except ValidationError:
        return False


def parse_reply(output: str) -> str:
    """Return the reply from `agy`'s stream-json output, from its last `result` event.

    Args:
        output (str): The standard output, one JSON event per line.

    Returns:
        str: The `response` of the last `result` event.

    Raises:
        GuideError: If a line is not a JSON object, no line is a `result` event, a `result` event is malformed, the
            result's `status` is not `SUCCESS`, its `finished_reason` is set, or its `response` is blank.
    """
    result: AgyResult | None = None
    for number, line in enumerate(output.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = AgyEvent.model_validate_json(line)
        except ValidationError as exc:
            raise GuideError(f"agy printed a line that is not a JSON object (line {number}): {line[:200]}") from exc
        if event.event == "result":
            if event.result is None:
                raise GuideError(f"agy printed a result event without a result (line {number}).")
            try:
                result = AgyResult.model_validate(event.result)
            except ValidationError as exc:
                raise GuideError(f"agy printed a malformed result event (line {number}): {line[:200]}") from exc
    if result is None:
        raise GuideError("agy printed no result event.")
    if result.status != "SUCCESS":
        raise GuideError(f"agy reported status {result.status}: {result.error or 'no error message'}")
    if result.finished_reason:
        raise GuideError(f"agy ended the turn early: {result.finished_reason}")
    if not result.response.strip():
        raise GuideError("agy returned an empty reply.")
    return result.response


class AgyCliOptions(BaseModel):
    """Settings from the `[guide.options.agy-cli]` table.

    Attributes:
        command (list[str]): Command that runs Antigravity CLI.
        model (str): Gemini model ID that ends in its effort level, such as `gemini-3.8-flash-medium` (`agy models`
            lists them), always passed as `--model`. The ID names the level, so there is no `effort` option.
        timeout_seconds (float): How long one generation may take.
        extra_args (list[str]): More arguments, added last. They cannot hold `--model` or `--effort`: the front matter
            records the `model` option.
    """

    model_config = ConfigDict(extra="forbid")

    command: list[str] = Field(default=["agy"], description="Command that runs Antigravity CLI.")
    model: str = Field(
        default=DEFAULT_MODEL,
        description="Gemini model ID ending in its effort level (see agy models), always passed as --model.",
    )
    timeout_seconds: float = Field(default=1800, description="How long one generation may take.")
    extra_args: list[str] = Field(
        default=[], description="More arguments for agy, added last; not --model or --effort."
    )

    @field_validator("model")
    @classmethod
    def check_model(cls, value: str) -> str:
        """Accept only a Gemini model ID that ends in its effort level, such as `gemini-3.8-flash-high`.

        Args:
            value (str): The configured model.

        Returns:
            str: The model, unchanged.

        Raises:
            ValueError: If the model is not a Gemini model ID that ends in `-low`, `-medium`, `-high`, `-xhigh` or
                `-max`.
        """
        if not MODEL_PATTERN.fullmatch(value):
            hint = f"such as {DEFAULT_MODEL} (agy models lists them), not {value!r}"
            raise ValueError(f"model must be a Gemini model ID that ends in its effort level, {hint}")
        return value

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


class AgyCliGenerator:
    """Generates a guide with `agy` in print mode, through a custom agent that has no tools.

    Each run writes the agent file into a temporary directory and adds that directory with `--add-dir`, so the video's
    work directory gets no `.agents/` folder. The process runs in the work directory. `agy` keeps every conversation,
    prompt included, under `~/.gemini/antigravity-cli/`; no option turns that off. `run_agy` stops `agy` if it hangs
    after printing its result.

    Args:
        options (AgyCliOptions): Settings.
        runner (PromptRunner): Function that runs the command. Replace it in tests.
    """

    def __init__(self, options: AgyCliOptions, runner: PromptRunner = run_agy) -> None:
        self.options: AgyCliOptions = options
        self.runner: PromptRunner = runner

    @property
    def harness(self) -> str:
        """str: `Antigravity CLI`."""
        return HARNESS

    @property
    def model(self) -> str:
        """str: The `model` option, passed as `--model`."""
        return self.options.model

    @property
    def effort(self) -> None:
        """None: `agy-cli` has no effort setting; the model ID names the level."""
        return None

    def build_args(self, agent_dir: Path) -> list[str]:
        """Build the command line.

        Args:
            agent_dir (Path): The directory that holds `.agents/agents/<AGENT_NAME>.md`.

        Returns:
            list[str]: The command and its arguments.
        """
        return [
            *self.options.command,
            "--add-dir",
            str(agent_dir),
            "--agent",
            AGENT_NAME,
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--disable-slash-commands",
            "--model",
            self.options.model,
            *self.options.extra_args,
        ]

    def generate(self, prompt: str, video: VideoInfo, work_dir: Path) -> str:
        """Run Antigravity CLI on the prompt and return its reply.

        Args:
            prompt (str): The full prompt.
            video (VideoInfo): The video. Not used: `agy` names its conversations itself.
            work_dir (Path): The video's work directory, used as the working directory.

        Returns:
            str: The reply.

        Raises:
            GuideError: If the command is missing, times out, fails or prints nothing, or its output holds no
                successful reply (see `parse_reply`).
        """
        del video
        with tempfile.TemporaryDirectory(prefix="ces-agy-") as temp:
            agent_dir = Path(temp)
            agent_file = agent_dir / ".agents" / "agents" / f"{AGENT_NAME}.md"
            agent_file.parent.mkdir(parents=True)
            _ = agent_file.write_text(agent_file_text(), encoding="utf-8")
            output = run_prompt_command(
                self.runner,
                self.build_args(agent_dir),
                stream_json_message(prompt),
                work_dir,
                self.options.timeout_seconds,
                {},
                display_name="agy",
                install_hint="Is Antigravity CLI installed and on PATH?",
                options_table=f"guide.options.{GENERATOR_NAME}",
            )
        return parse_reply(output)
