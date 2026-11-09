"""Guide generator that runs the `pi` CLI (the Pi coding agent) in JSON mode, with everything but the model turned off."""

import os
import subprocess
from pathlib import Path
from typing import Literal, get_args

from pydantic import Field, BaseModel, ConfigDict, ValidationError, field_validator

from .base import SYSTEM_PROMPT, refuse_recorded_flags
from ._subprocess import PromptRunner, run_prompt_command
from ..guide import GuideError
from ..video import VideoInfo

GENERATOR_NAME = "pi-cli"
HARNESS = "Pi"
OPTIONS_TABLE = f"guide.options.{GENERATOR_NAME}"

# The levels `pi --help` lists for `--thinking`.
ThinkingLevel = Literal["off", "minimal", "low", "medium", "high", "xhigh", "max"]
THINKING_LEVELS: frozenset[str] = frozenset(get_args(ThinkingLevel))

DEFAULT_MODEL = "openai/gpt-6.1-terra"
DEFAULT_EFFORT: ThinkingLevel = "high"
EXTRA_ARGS_HINTS = {
    "--model": "set the model option instead",
    "--provider": "set the model option instead",
    "--models": "set the model option instead",
    "--thinking": "set the effort option instead",
    "--mode": "pi-cli reads pi's JSON output",
}
# Everything a guide does not need: tools, extensions, skills, prompt templates, themes, context files (AGENTS.md,
# CLAUDE.md), MCP servers, trust-gated project files (.pi/), and the start-up catalog refresh and version check.
LOCKDOWN_FLAGS = (
    "--no-tools",
    "--no-extensions",
    "--no-skills",
    "--no-prompt-templates",
    "--no-themes",
    "--no-context-files",
    "--no-mcp",
    "--no-approve",
    "--offline",
)
COMPACTION_EVENTS = frozenset({"compaction_start", "compaction_end"})


def run_pi(
    args: list[str], prompt: str, cwd: Path, timeout: float, extra_env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """Run the `pi` CLI with the prompt on stdin.

    The pipes use UTF-8, whatever the locale, since `pi` prints JSON. Bytes that are not UTF-8 become U+FFFD.

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
        args,
        input=prompt,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        cwd=cwd,
        timeout=timeout,
        env=env,
        check=False,
    )


class PiContentBlock(BaseModel):
    """One content block of a `pi` message. Other fields, such as a thinking block's `thinking`, are ignored.

    Attributes:
        type (str): The block type, such as `text` or `thinking`.
        text (str | None): The text, in a `text` block.
    """

    type: str
    text: str | None = None


class PiMessageRole(BaseModel):
    """The role of the message in a `message_end` event. Other fields are ignored.

    Attributes:
        role (str | None): `user`, `assistant`, or another role.
    """

    role: str | None = None


class PiAssistantMessage(BaseModel):
    """An assistant message from a `message_end` event in `pi`'s JSON output. Other fields are ignored.

    Attributes:
        provider (str): The provider that ran, such as `openai`.
        model (str): The model ID within the provider, such as `gpt-6.1-terra`.
        thinking_level (str | None): The thinking level that ran, after `pi` clamped it to what the model supports.
        stop_reason (str): `stop` for a complete reply; `length`, `error`, `aborted` or `toolUse` otherwise.
        error_message (str | None): Why the request failed, when it did.
        content (list[PiContentBlock]): The text, thinking and other blocks.
    """

    provider: str
    model: str
    thinking_level: str | None = Field(default=None, alias="thinkingLevel")
    stop_reason: str = Field(alias="stopReason")
    error_message: str | None = Field(default=None, alias="errorMessage")
    content: list[PiContentBlock] = []


class PiEvent(BaseModel):
    """One line of `pi`'s JSON output. Only a few fields are read, so other events may have any shape.

    `type` and `reason` must be strings when present.

    Attributes:
        type (str | None): The event type, such as `session`, `message_update`, `message_end` or `compaction_start`.
        reason (str | None): Why compaction began, on a compaction event: `manual`, `threshold` or `overflow`.
        message (object): The message, on a message event. Checked against `PiAssistantMessage` only on a
            `message_end` event whose role is `assistant`.
    """

    type: str | None = None
    reason: str | None = None
    message: object = None


def read_assistant_message(event: PiEvent, number: int, line: str) -> PiAssistantMessage | None:
    """Read the assistant message from a `message_end` event.

    Args:
        event (PiEvent): The event.
        number (int): The event's line number, for error messages.
        line (str): The event's line, for error messages.

    Returns:
        PiAssistantMessage | None: The message, or None if the event is not a `message_end` of an assistant message.

    Raises:
        GuideError: If the event's message is not a JSON object, or is an assistant message that lacks a field.
    """
    if event.type != "message_end":
        return None
    try:
        if PiMessageRole.model_validate(event.message).role != "assistant":
            return None
        return PiAssistantMessage.model_validate(event.message)
    except ValidationError as exc:
        raise GuideError(f"pi printed a malformed message_end event (line {number}): {line[:200]}") from exc


def parse_reply(output: str, model: str, effort: str) -> str:
    """Return the reply from `pi`'s JSON output, from the last assistant message, after checking how it ran.

    Lines are split on LF only: `pi`'s JSON strings may hold U+2028 and U+2029, which `str.splitlines()` would also
    split on.

    Args:
        output (str): The standard output, one JSON event per line.
        model (str): The configured model, `provider/id`.
        effort (str): The configured thinking level.

    Returns:
        str: The text blocks of the last assistant message, joined with newlines as `pi`'s text mode prints them.
            Thinking blocks are left out.

    Raises:
        GuideError: If a line is not a JSON object, `pi` compacted the conversation, no assistant message ended, the
            last one did not stop normally, it ran another model or thinking level, or its text is blank.
    """
    reply: PiAssistantMessage | None = None
    for number, raw_line in enumerate(output.split("\n"), start=1):
        line = raw_line.removesuffix("\r")
        if not line.strip():
            continue
        try:
            event = PiEvent.model_validate_json(line)
        except ValidationError as exc:
            raise GuideError(f"pi printed a line that is not a JSON object (line {number}): {line[:200]}") from exc
        if event.type in COMPACTION_EVENTS:
            raise GuideError(
                f"pi compacted the conversation (reason: {event.reason or 'not given'}), so part of the prompt was "
                + "replaced by a summary. Choose a model with a larger context window."
            )
        message = read_assistant_message(event, number, line)
        if message is not None:
            reply = message
    if reply is None:
        raise GuideError("pi printed no reply.")
    if reply.stop_reason == "length":
        raise GuideError("pi's reply hit the model's output limit, so the guide is cut short.")
    if reply.stop_reason != "stop":
        raise GuideError(f"pi stopped with reason {reply.stop_reason}: {reply.error_message or 'no error message'}")
    ran = f"{reply.provider}/{reply.model}"
    if ran != model:
        raise GuideError(
            f"pi ran {ran}, not the configured model {model}. Set model in [{OPTIONS_TABLE}] to a provider and "
            + "model ID exactly as `pi --list-models` lists them."
        )
    if reply.thinking_level is None:
        raise GuideError("pi did not report the thinking level.")
    if reply.thinking_level != effort:
        raise GuideError(
            f"pi ran {model} with thinking level {reply.thinking_level}, not the configured effort {effort}; the "
            + f"model may not support that level. Choose another effort in [{OPTIONS_TABLE}]."
        )
    text = "\n".join(block.text for block in reply.content if block.type == "text" and block.text is not None)
    if not text.strip():
        raise GuideError("pi returned an empty reply.")
    return text


class PiCliOptions(BaseModel):
    """Settings from the `[guide.options.pi-cli]` table.

    Attributes:
        command (list[str]): Command that runs the Pi coding agent.
        model (str): `provider/id`, such as `openai/gpt-6.1-terra` (`pi --list-models` lists them), always passed as
            `--model`. `pi` matches a model fuzzily, so the model that ran is checked after the run.
        effort (ThinkingLevel): Thinking level, always passed as `--thinking`, and checked after the run.
        timeout_seconds (float): How long one generation may take.
        session_dir (Path | None): Folder to run `pi` in with session logging on, so every log lands in one folder
            under `~/.pi/agent/sessions/`. None passes `--no-session` and keeps no log.
        extra_args (list[str]): More arguments, added last. They cannot hold `--model`, `--provider`, `--models`,
            `--thinking` or `--mode`.
    """

    model_config = ConfigDict(extra="forbid")

    command: list[str] = Field(default=["pi"], description="Command that runs the Pi coding agent.")
    model: str = Field(
        default=DEFAULT_MODEL,
        description="provider/id, as pi --list-models lists them, always passed as --model and checked after the run.",
    )
    effort: ThinkingLevel = Field(
        default=DEFAULT_EFFORT, description="Thinking level, always passed as --thinking and checked after the run."
    )
    timeout_seconds: float = Field(default=1800, description="How long one generation may take.")
    session_dir: Path | None = Field(
        default=None, description="Run pi here with session logging on. Unset keeps no session log."
    )
    extra_args: list[str] = Field(
        default=[],
        description="More arguments for pi, added last; not --model, --provider, --models, --thinking or --mode.",
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

    @field_validator("model")
    @classmethod
    def check_model(cls, value: str) -> str:
        """Accept only `provider/id`, without a thinking-level suffix such as `:low`.

        Args:
            value (str): The configured model.

        Returns:
            str: The model, unchanged.

        Raises:
            ValueError: If the model holds whitespace, the provider or the ID is empty, or the ID ends in
                `:<level>` for a thinking level.
        """
        if any(char.isspace() for char in value):
            raise ValueError(f"model cannot contain whitespace: {value!r}")
        provider, slash, model_id = value.partition("/")
        if not (provider and slash and model_id):
            raise ValueError(
                f"model must be a provider and a model ID, such as {DEFAULT_MODEL} (pi --list-models lists them), "
                + f"not {value!r}"
            )
        _, colon, suffix = model_id.rpartition(":")
        if colon and suffix in THINKING_LEVELS:
            raise ValueError(f"model cannot end in :{suffix}: set the effort option instead")
        return value

    @field_validator("extra_args")
    @classmethod
    def check_extra_args(cls, value: list[str]) -> list[str]:
        """Refuse flags that change the model, the thinking level or the output mode, alone or as `--flag=...`.

        Args:
            value (list[str]): The configured arguments.

        Returns:
            list[str]: The arguments, unchanged.

        Raises:
            ValueError: If an argument is `--model`, `--provider`, `--models`, `--thinking` or `--mode`, with or
                without `=...`.
        """
        return refuse_recorded_flags(value, EXTRA_ARGS_HINTS)


class PiCliGenerator:
    """Generates a guide with `pi` in JSON mode, with tools, extensions and context files turned off.

    Without `session_dir` the process runs in the video's work directory and keeps no session log. With it, the
    process runs in `session_dir` and each logged session is named after the video ID. `pi`'s prompt caching stays
    on: no flag or documented setting turns it off.

    Args:
        options (PiCliOptions): Settings.
        runner (PromptRunner): Function that runs the command. Replace it in tests.
    """

    def __init__(self, options: PiCliOptions, runner: PromptRunner = run_pi) -> None:
        self.options: PiCliOptions = options
        self.runner: PromptRunner = runner

    @property
    def harness(self) -> str:
        """str: `Pi`."""
        return HARNESS

    @property
    def model(self) -> str:
        """str: The `model` option, passed as `--model`."""
        return self.options.model

    @property
    def effort(self) -> str:
        """str: The `effort` option, passed as `--thinking`."""
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
            "--mode",
            "json",
        ]
        if self.options.session_dir is None:
            args.append("--no-session")
        elif video_id:
            args += ["--name", video_id]
        return [
            *args,
            *LOCKDOWN_FLAGS,
            "--system-prompt",
            SYSTEM_PROMPT,
            "--model",
            self.options.model,
            "--thinking",
            self.options.effort,
            *self.options.extra_args,
        ]

    def generate(self, prompt: str, video: VideoInfo, work_dir: Path) -> str:
        """Run the Pi coding agent on the prompt and return its reply.

        Args:
            prompt (str): The full prompt.
            video (VideoInfo): The video. Its ID names the logged session.
            work_dir (Path): The video's work directory. It is the working directory unless `session_dir` is set.

        Returns:
            str: The reply.

        Raises:
            GuideError: If `session_dir` is not a folder, or the command is missing, times out, fails or prints
                nothing, or its output holds no complete reply from the configured model and thinking level (see
                `parse_reply`).
        """
        args = self.build_args(video.id)
        cwd = work_dir
        if self.options.session_dir is not None:
            cwd = self.options.session_dir
            try:
                cwd.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise GuideError(f"Cannot use session_dir {cwd}: {exc}") from exc
        output = run_prompt_command(
            self.runner,
            args,
            prompt,
            cwd,
            self.options.timeout_seconds,
            {},
            display_name="pi",
            install_hint="Is the Pi coding agent installed and on PATH?",
            options_table=OPTIONS_TABLE,
        )
        return parse_reply(output, self.options.model, self.options.effort)
