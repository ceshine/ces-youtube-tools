import sys
import json
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from ces_youtube_tools.guide import GuideError
from ces_youtube_tools.video import VideoInfo
from ces_youtube_tools.generators.base import SYSTEM_PROMPT
from ces_youtube_tools.generators.pi_cli import (
    run_pi,
    parse_reply,
    PiCliOptions,
    DEFAULT_MODEL,
    PiCliGenerator,
)

VIDEO = VideoInfo(id="abc123", title="A video", duration=60)


def assistant_message(
    text: str | None = "# Guide\n",
    *,
    provider: str = "openai",
    model: str = "gpt-6.1-terra",
    thinking_level: str | None = "high",
    stop_reason: str = "stop",
    error_message: str | None = None,
    content: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    """Return an assistant message as `pi` prints it in a `message_end` event."""
    message: dict[str, object] = {
        "role": "assistant",
        "content": content if content is not None else ([{"type": "text", "text": text}] if text is not None else []),
        "api": "openai-responses",
        "provider": provider,
        "model": model,
        "usage": {"input": 3, "output": 10, "cacheRead": 0, "cacheWrite": 97},
        "stopReason": stop_reason,
        "timestamp": 1794000000000,
    }
    if thinking_level is not None:
        message["thinkingLevel"] = thinking_level
    if error_message is not None:
        message["errorMessage"] = error_message
    return message


def event_line(event_type: str, **fields: object) -> str:
    # pi writes U+2028, U+2029 and other non-ASCII characters as they are, not as \u escapes.
    return json.dumps({"type": event_type, **fields}, ensure_ascii=False)


def pi_output(*assistant_messages: dict[str, object], before_reply: tuple[str, ...] = ()) -> str:
    """Build `pi`'s JSON output: a session header, the system and user messages, then each assistant message."""
    lines = [
        event_line("session", version=3, id="s1", cwd="/work"),
        event_line("agent_start"),
        event_line("message_end", message={"role": "system", "content": "SYSTEM", "timestamp": 1}),
        event_line("message_end", message={"role": "user", "content": [{"type": "text", "text": "PROMPT"}]}),
        *before_reply,
    ]
    for message in assistant_messages:
        lines += [
            event_line("message_start", message={**message, "content": []}),
            event_line("message_update", message=message, assistantMessageEvent={"type": "text_delta", "delta": "x"}),
            event_line("message_end", message=message),
        ]
    lines.append(event_line("agent_settled", aborted=False))
    return "\n".join(lines) + "\n"


class FakePi:
    def __init__(
        self, stdout: str | None = None, returncode: int = 0, stderr: str = "", error: Exception | None = None
    ):
        self.calls: list[tuple[list[str], str, Path, float]] = []
        self.envs: list[dict[str, str]] = []
        self.result: tuple[str, int, str] = (
            pi_output(assistant_message()) if stdout is None else stdout,
            returncode,
            stderr,
        )
        self.error: Exception | None = error

    def __call__(
        self, args: list[str], prompt: str, cwd: Path, timeout: float, extra_env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((args, prompt, cwd, timeout))
        self.envs.append(extra_env)
        if self.error is not None:
            raise self.error
        stdout, returncode, stderr = self.result
        return subprocess.CompletedProcess(args, returncode, stdout, stderr)


def test_generator_reports_harness_model_and_effort() -> None:
    generator = PiCliGenerator(PiCliOptions(model="openrouter/openai/gpt-6.1-terra", effort="low"))
    assert (generator.harness, generator.model, generator.effort) == ("Pi", "openrouter/openai/gpt-6.1-terra", "low")


def test_build_args_defaults() -> None:
    assert PiCliGenerator(PiCliOptions()).build_args() == [
        "pi",
        "--mode",
        "json",
        "--no-session",
        "--no-tools",
        "--no-extensions",
        "--no-skills",
        "--no-prompt-templates",
        "--no-themes",
        "--no-context-files",
        "--no-mcp",
        "--no-approve",
        "--offline",
        "--system-prompt",
        SYSTEM_PROMPT,
        "--model",
        "openai/gpt-6.1-terra",
        "--thinking",
        "high",
    ]


def test_build_args_with_session_dir_names_the_session(tmp_path: Path) -> None:
    args = PiCliGenerator(PiCliOptions(session_dir=tmp_path)).build_args("abc123")
    assert "--no-session" not in args
    assert args[3:5] == ["--name", "abc123"]


def test_build_args_with_session_dir_and_no_video_id(tmp_path: Path) -> None:
    args = PiCliGenerator(PiCliOptions(session_dir=tmp_path)).build_args()
    assert "--no-session" not in args
    assert "--name" not in args


def test_build_args_with_options() -> None:
    options = PiCliOptions(
        command=["npx", "pi"], model="anthropic/claude-fable-5-1", effort="max", extra_args=["--verbose"]
    )
    args = PiCliGenerator(options).build_args()
    assert args[:2] == ["npx", "pi"]
    assert args[-5:] == ["--model", "anthropic/claude-fable-5-1", "--thinking", "max", "--verbose"]


def test_generate_sends_the_prompt_unchanged_from_the_work_dir(tmp_path: Path) -> None:
    fake = FakePi()
    reply = PiCliGenerator(PiCliOptions(timeout_seconds=60), fake).generate("PROMPT\n", VIDEO, tmp_path)
    assert reply == "# Guide\n"
    assert [call[1:] for call in fake.calls] == [("PROMPT\n", tmp_path, 60)]
    assert fake.envs == [{}]


def test_generate_runs_in_session_dir_and_creates_it(tmp_path: Path) -> None:
    work_dir = tmp_path / "work"
    session_dir = tmp_path / "sessions" / "pi"
    fake = FakePi()
    _ = PiCliGenerator(PiCliOptions(session_dir=session_dir), fake).generate("PROMPT", VIDEO, work_dir)
    args, _, cwd, _ = fake.calls[0]
    assert cwd == session_dir
    assert session_dir.is_dir()
    assert "--no-session" not in args
    assert args[3:5] == ["--name", "abc123"]


def test_generate_without_session_dir_runs_in_the_work_dir(tmp_path: Path) -> None:
    fake = FakePi()
    _ = PiCliGenerator(PiCliOptions(), fake).generate("PROMPT", VIDEO, tmp_path)
    args, _, cwd, _ = fake.calls[0]
    assert cwd == tmp_path
    assert "--no-session" in args
    assert "--name" not in args


def test_generate_rejects_a_session_dir_that_is_a_file(tmp_path: Path) -> None:
    session_dir = tmp_path / "not-a-dir"
    _ = session_dir.write_text("x", encoding="utf-8")
    fake = FakePi()
    with pytest.raises(GuideError, match="Cannot use session_dir"):
        _ = PiCliGenerator(PiCliOptions(session_dir=session_dir), fake).generate("PROMPT", VIDEO, tmp_path)
    assert fake.calls == []


def test_session_dir_expands_the_home_folder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    options = PiCliOptions.model_validate({"session_dir": "~/sessions"})
    assert options.session_dir == tmp_path / "sessions"


def test_generate_checks_the_configured_model_and_effort(tmp_path: Path) -> None:
    message = assistant_message(provider="openrouter", model="openai/gpt-6.1-terra", thinking_level="low")
    options = PiCliOptions(model="openrouter/openai/gpt-6.1-terra", effort="low")
    assert PiCliGenerator(options, FakePi(stdout=pi_output(message))).generate("PROMPT", VIDEO, tmp_path) == "# Guide\n"
    with pytest.raises(GuideError, match="thinking level low, not the configured effort high"):
        _ = PiCliGenerator(PiCliOptions(), FakePi(stdout=pi_output(assistant_message(thinking_level="low")))).generate(
            "PROMPT", VIDEO, tmp_path
        )


@pytest.mark.parametrize(
    ("fake", "message"),
    [
        (FakePi(error=FileNotFoundError("pi")), "Is the Pi coding agent installed"),
        (FakePi(error=subprocess.TimeoutExpired(["pi"], 60)), r"\[guide.options.pi-cli\]"),
        (FakePi(returncode=1, stderr='Model "terra" not found'), 'exited with status 1: Model "terra" not found'),
        (FakePi(stdout="  \n"), "pi returned an empty reply"),
        (
            FakePi(stdout=pi_output(assistant_message(None, stop_reason="error", error_message="404 not found"))),
            "stopped with reason error: 404 not found",
        ),
    ],
)
def test_generate_errors(tmp_path: Path, fake: FakePi, message: str) -> None:
    with pytest.raises(GuideError, match=message):
        _ = PiCliGenerator(PiCliOptions(), fake).generate("PROMPT", VIDEO, tmp_path)


def fake_pi_command(body: str) -> list[str]:
    """Return a command that runs `body` as a Python script."""
    return [sys.executable, "-c", f"import os, sys\n{body}"]


def test_run_pi_sends_the_prompt_from_cwd_with_extra_env(tmp_path: Path) -> None:
    body = "print(sys.stdin.read(), os.getcwd(), os.environ['EXTRA'], 'PATH' in os.environ, sep='|')"
    completed = run_pi(fake_pi_command(body), "PROMPT \u2028 café", tmp_path, 30, {"EXTRA": "x"})
    assert completed.returncode == 0
    assert completed.stdout == f"PROMPT \u2028 café|{tmp_path}|x|True\n"


def test_run_pi_replaces_bytes_that_are_not_utf8(tmp_path: Path) -> None:
    body = "sys.stdout.buffer.write(b'\\xff caf\\xc3\\xa9\\n'); sys.stderr.buffer.write(b'\\xfe'); sys.exit(1)"
    completed = run_pi(fake_pi_command(body), "PROMPT", tmp_path, 30, {})
    assert (completed.returncode, completed.stdout, completed.stderr) == (1, "� café\n", "�")


def test_run_pi_times_out(tmp_path: Path) -> None:
    with pytest.raises(subprocess.TimeoutExpired):
        _ = run_pi(fake_pi_command("import time; time.sleep(60)"), "PROMPT", tmp_path, 0.5, {})


def test_options_reject_unknown_keys() -> None:
    with pytest.raises(ValidationError):
        _ = PiCliOptions.model_validate({"prompt_caching": False})


def test_options_reject_unknown_thinking_levels() -> None:
    with pytest.raises(ValidationError):
        _ = PiCliOptions.model_validate({"effort": "bogus"})


@pytest.mark.parametrize(
    "model",
    [
        DEFAULT_MODEL,
        "openrouter/openai/gpt-6.1-terra",
        "openrouter/anthropic/claude-fable-5.1:batch",
    ],
)
def test_options_accept_provider_and_model_id(model: str) -> None:
    assert PiCliOptions(model=model).model == model


@pytest.mark.parametrize("model", ["", "terra", "/x", "openai/"])
def test_options_reject_a_model_without_a_provider_and_id(model: str) -> None:
    with pytest.raises(ValidationError, match="model must be a provider and a model ID"):
        _ = PiCliOptions(model=model)


@pytest.mark.parametrize(
    "model", ["openai/gpt-6.1-terra ", " openai/gpt-6.1-terra", "openai /gpt-6.1-terra", "openai/\t"]
)
def test_options_reject_a_model_with_whitespace(model: str) -> None:
    with pytest.raises(ValidationError, match="model cannot contain whitespace"):
        _ = PiCliOptions(model=model)


@pytest.mark.parametrize("model", ["openai/gpt-6.1-terra:low", "openrouter/openai/gpt-6.1-terra:xhigh"])
def test_options_reject_a_thinking_level_suffix(model: str) -> None:
    with pytest.raises(ValidationError, match="model cannot end in :.*set the effort option instead"):
        _ = PiCliOptions(model=model)


@pytest.mark.parametrize(
    ("arg", "hint"),
    [
        ("--model", "set the model option instead"),
        ("--model=openai/gpt-6.1-terra", "set the model option instead"),
        ("--provider", "set the model option instead"),
        ("--provider=openai", "set the model option instead"),
        ("--models", "set the model option instead"),
        ("--models=openai/*", "set the model option instead"),
        ("--thinking", "set the effort option instead"),
        ("--thinking=low", "set the effort option instead"),
        ("--mode", "pi-cli reads pi's JSON output"),
        ("--mode=text", "pi-cli reads pi's JSON output"),
    ],
)
def test_options_reject_recorded_flags_in_extra_args(arg: str, hint: str) -> None:
    with pytest.raises(ValidationError, match=f"extra_args cannot hold {arg.split('=')[0]}: {hint}"):
        _ = PiCliOptions(extra_args=["--verbose", arg])


def test_parse_reply_returns_the_assistant_text() -> None:
    assert parse_reply(pi_output(assistant_message("# The guide\n\nText.")), DEFAULT_MODEL, "high") == (
        "# The guide\n\nText."
    )


def test_parse_reply_takes_the_last_assistant_message() -> None:
    failed = assistant_message(None, stop_reason="error", error_message="overloaded")
    output = pi_output(failed, assistant_message("second"), before_reply=(event_line("auto_retry_start", attempt=1),))
    assert parse_reply(output, DEFAULT_MODEL, "high") == "second"


def test_parse_reply_keeps_line_and_paragraph_separators_in_strings() -> None:
    text = "# Guide\u2028still the first line\u2029and the next\x85end"
    output = pi_output(assistant_message(text))
    assert len(output.splitlines()) > len(output.split("\n"))
    assert parse_reply(output, DEFAULT_MODEL, "high") == text


def test_parse_reply_accepts_crlf_line_ends() -> None:
    assert parse_reply(pi_output(assistant_message()).replace("\n", "\r\n"), DEFAULT_MODEL, "high") == "# Guide\n"


def test_parse_reply_leaves_out_thinking_and_joins_text_blocks() -> None:
    content: list[dict[str, object]] = [
        {"type": "thinking", "thinking": "Plan the segments."},
        {"type": "text", "text": "# Guide"},
        {"type": "thinking", "thinking": "More."},
        {"type": "text", "text": "Overview."},
    ]
    assert parse_reply(pi_output(assistant_message(content=content)), DEFAULT_MODEL, "high") == "# Guide\nOverview."


def test_parse_reply_skips_events_of_any_other_shape() -> None:
    output = pi_output(
        assistant_message(),
        before_reply=(json.dumps({"no_type": True}), event_line("tool_execution_end", message="text", result=[1])),
    )
    assert parse_reply(output, DEFAULT_MODEL, "high") == "# Guide\n"


@pytest.mark.parametrize(
    ("output", "model", "effort", "message"),
    [
        ("not json\n", DEFAULT_MODEL, "high", r"not a JSON object \(line 1\): not json"),
        (json.dumps(["a", "list"]), DEFAULT_MODEL, "high", "not a JSON object"),
        (pi_output(), DEFAULT_MODEL, "high", "pi printed no reply"),
        (event_line("message_end", message="text"), DEFAULT_MODEL, "high", r"malformed message_end event \(line 1\)"),
        (
            event_line("message_end", message={"role": "assistant", "content": []}),
            DEFAULT_MODEL,
            "high",
            "malformed message_end event",
        ),
        (
            pi_output(assistant_message(), before_reply=(event_line("compaction_start", reason="overflow"),)),
            DEFAULT_MODEL,
            "high",
            r"pi compacted the conversation \(reason: overflow\).*larger context window",
        ),
        (
            pi_output(assistant_message()) + event_line("compaction_end", reason="threshold", result={}),
            DEFAULT_MODEL,
            "high",
            r"compacted the conversation \(reason: threshold\)",
        ),
        (pi_output(assistant_message(stop_reason="length")), DEFAULT_MODEL, "high", "hit the model's output limit"),
        (
            pi_output(assistant_message(None, stop_reason="aborted")),
            DEFAULT_MODEL,
            "high",
            "stopped with reason aborted: no error message",
        ),
        (
            pi_output(assistant_message(model="gpt-6.1-terra-pro")),
            DEFAULT_MODEL,
            "high",
            r"pi ran openai/gpt-6.1-terra-pro, not the configured model openai/gpt-6.1-terra.*pi --list-models",
        ),
        (
            pi_output(assistant_message(provider="openrouter", model="openai/gpt-6.1-terra")),
            DEFAULT_MODEL,
            "high",
            "pi ran openrouter/openai/gpt-6.1-terra, not the configured model",
        ),
        (
            pi_output(assistant_message(thinking_level=None)),
            DEFAULT_MODEL,
            "high",
            "pi did not report the thinking level",
        ),
        (
            pi_output(assistant_message(thinking_level="low")),
            DEFAULT_MODEL,
            "minimal",
            "thinking level low, not the configured effort minimal; the model may not support that level",
        ),
        (pi_output(assistant_message("  \n")), DEFAULT_MODEL, "high", "pi returned an empty reply"),
        (
            pi_output(assistant_message(content=[{"type": "thinking", "thinking": "Only thoughts."}])),
            DEFAULT_MODEL,
            "high",
            "pi returned an empty reply",
        ),
    ],
)
def test_parse_reply_errors(output: str, model: str, effort: str, message: str) -> None:
    with pytest.raises(GuideError, match=message):
        _ = parse_reply(output, model, effort)
