"""Guide generator that runs the `agy` CLI (Antigravity CLI) in print mode, with stream-json input and output."""

import os
import json
import tempfile
import subprocess
from pathlib import Path

from pydantic import Field, BaseModel, ConfigDict, ValidationError, field_validator

from .base import SYSTEM_PROMPT, refuse_recorded_flags
from ._subprocess import PromptRunner, run_prompt_command
from ..guide import GuideError
from ..video import VideoInfo

GENERATOR_NAME = "agy-cli"
HARNESS = "Antigravity CLI"
AGENT_NAME = "ces-guide-writer"
DEFAULT_MODEL = "gemini-3.8-flash-medium"
EXTRA_ARGS_HINTS = {
    "--model": "set the model option instead",
    "--effort": "choose a model ID that names the effort level, e.g. gemini-3.8-flash-high",
}


def run_agy(
    args: list[str], prompt: str, cwd: Path, timeout: float, extra_env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """Run the `agy` CLI with the prompt on stdin.

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
    env = {**os.environ, **extra_env}
    return subprocess.run(
        args, input=prompt, capture_output=True, text=True, cwd=cwd, timeout=timeout, env=env, check=False
    )


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
        model (str): Model ID, always passed as `--model`. The ID names the effort level too, e.g.
            `gemini-3.8-flash-medium`, so there is no `effort` option.
        timeout_seconds (float): How long one generation may take.
        extra_args (list[str]): More arguments, added last. They cannot hold `--model` or `--effort`: the front matter
            records the `model` option.
    """

    model_config = ConfigDict(extra="forbid")

    command: list[str] = Field(default=["agy"], description="Command that runs Antigravity CLI.")
    model: str = Field(
        default=DEFAULT_MODEL,
        min_length=1,
        description="Model ID, always passed as --model. It names the effort level too (e.g. ...-medium).",
    )
    timeout_seconds: float = Field(default=1800, description="How long one generation may take.")
    extra_args: list[str] = Field(
        default=[], description="More arguments for agy, added last; not --model or --effort."
    )

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
    prompt included, under `~/.gemini/antigravity-cli/`; no option turns that off.

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
