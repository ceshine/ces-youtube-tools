import sys
import json
import time
import logging
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from ces_youtube_tools.guide import GuideError
from ces_youtube_tools.video import VideoInfo
from ces_youtube_tools.generators import agy_cli
from ces_youtube_tools.generators.base import SYSTEM_PROMPT
from ces_youtube_tools.generators.agy_cli import (
    run_agy,
    AGENT_NAME,
    parse_reply,
    AgyCliOptions,
    DEFAULT_MODEL,
    MODEL_PATTERN,
    AgyCliGenerator,
    agent_file_text,
    is_result_event,
    stream_json_message,
)

VIDEO = VideoInfo(id="abc123", title="A video", duration=60)


def result_line(status: str = "SUCCESS", response: str = "# Guide\n", **extra: object) -> str:
    return json.dumps({"event": "result", "result": {"status": status, "response": response, **extra}})


class FakeAgy:
    def __init__(
        self, stdout: str | None = None, returncode: int = 0, stderr: str = "", error: Exception | None = None
    ):
        self.calls: list[tuple[list[str], str, Path, float]] = []
        self.envs: list[dict[str, str]] = []
        self.agent_files: list[str] = []
        self.result: tuple[str, int, str] = (result_line() if stdout is None else stdout, returncode, stderr)
        self.error: Exception | None = error

    def __call__(
        self, args: list[str], prompt: str, cwd: Path, timeout: float, extra_env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((args, prompt, cwd, timeout))
        self.envs.append(extra_env)
        agent_dir = Path(args[args.index("--add-dir") + 1])
        self.agent_files.append((agent_dir / ".agents" / "agents" / f"{AGENT_NAME}.md").read_text(encoding="utf-8"))
        if self.error is not None:
            raise self.error
        stdout, returncode, stderr = self.result
        return subprocess.CompletedProcess(args, returncode, stdout, stderr)


def test_generator_reports_harness_model_and_effort() -> None:
    generator = AgyCliGenerator(AgyCliOptions(model="gemini-3.8-flash-high"))
    assert (generator.harness, generator.model, generator.effort) == ("Antigravity CLI", "gemini-3.8-flash-high", None)


def test_build_args_defaults(tmp_path: Path) -> None:
    assert AgyCliGenerator(AgyCliOptions()).build_args(tmp_path) == [
        "agy",
        "--add-dir",
        str(tmp_path),
        "--agent",
        "ces-guide-writer",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--disable-slash-commands",
        "--model",
        "gemini-3.8-flash-medium",
    ]


def test_build_args_with_options(tmp_path: Path) -> None:
    options = AgyCliOptions(command=["npx", "agy"], model="gemini-3.8-flash-high", extra_args=["--sandbox"])
    args = AgyCliGenerator(options).build_args(tmp_path)
    assert args[:2] == ["npx", "agy"]
    assert args[-3:] == ["--model", "gemini-3.8-flash-high", "--sandbox"]


def test_options_reject_unknown_keys() -> None:
    with pytest.raises(ValidationError):
        _ = AgyCliOptions.model_validate({"effort": "high"})


def test_the_default_model_is_accepted() -> None:
    assert MODEL_PATTERN.fullmatch(DEFAULT_MODEL)


@pytest.mark.parametrize("model", ["gemini-3.8-flash-low", "gemini-3.8-flash-high", "gemini-4-pro-xhigh"])
def test_options_accept_gemini_models_with_an_effort_level(model: str) -> None:
    assert AgyCliOptions(model=model).model == model


@pytest.mark.parametrize(
    "model",
    [
        "",
        "gemini-3.8-flash",
        "gemini---high",
        "gemini-3.8-flash-medium ",
        "Gemini-3.8-Flash-High",
        "claude-sonnet-4-6",
        "claude-opus-4-6-thinking",
        "gpt-oss-120b-medium",
    ],
)
def test_options_reject_other_models(model: str) -> None:
    with pytest.raises(ValidationError, match="must be a Gemini model ID that ends in its effort level"):
        _ = AgyCliOptions(model=model)


@pytest.mark.parametrize("arg", ["--model", "--model=gemini", "--effort", "--effort=high"])
def test_options_reject_model_and_effort_in_extra_args(arg: str) -> None:
    with pytest.raises(ValidationError, match="extra_args cannot hold"):
        _ = AgyCliOptions(extra_args=["--sandbox", arg])


def test_options_say_how_to_choose_the_effort_level() -> None:
    with pytest.raises(ValidationError, match="choose a model ID that names the effort level"):
        _ = AgyCliOptions(extra_args=["--effort=high"])


def test_agent_file_has_no_tools_and_the_system_prompt() -> None:
    text = agent_file_text()
    assert text.startswith("---\nname: ces-guide-writer\n")
    assert "\ntools: []\n" in text
    assert "\nexcludeDefaultComponents: true\n" in text
    assert "\nmainAgent: true\n" in text
    assert text.endswith(f"---\n# Guide writer\n\n{SYSTEM_PROMPT}\n")


def test_stream_json_message_is_one_line() -> None:
    line = stream_json_message('Line one\nLine "two"')
    assert line.endswith("\n")
    assert "\n" not in line[:-1]
    assert json.loads(line) == {"event": "user", "message": {"content": 'Line one\nLine "two"'}}


def test_generate_sends_the_prompt_as_stream_json_from_the_work_dir(tmp_path: Path) -> None:
    fake = FakeAgy()
    reply = AgyCliGenerator(AgyCliOptions(timeout_seconds=60), fake).generate("PROMPT", VIDEO, tmp_path)
    assert reply == "# Guide\n"
    args, stdin, cwd, timeout = fake.calls[0]
    assert (stdin, cwd, timeout) == (stream_json_message("PROMPT"), tmp_path, 60)
    assert fake.envs == [{}]
    assert fake.agent_files == [agent_file_text()]
    agent_dir = Path(args[args.index("--add-dir") + 1])
    assert not agent_dir.exists()
    assert not (tmp_path / ".agents").exists()


def test_generate_returns_only_the_result_response(tmp_path: Path) -> None:
    stdout = "\n".join(
        [
            json.dumps({"event": "init", "tools": ["read_file"]}),
            json.dumps({"event": "step_update", "step": {"text": "Thinking"}}),
            "",
            result_line(response="# The guide\n\nText.", usage={"input_tokens": 445}),
        ]
    )
    reply = AgyCliGenerator(AgyCliOptions(), FakeAgy(stdout=stdout)).generate("PROMPT", VIDEO, tmp_path)
    assert reply == "# The guide\n\nText."


def test_parse_reply_skips_events_of_any_other_shape() -> None:
    stdout = "\n".join(
        [
            json.dumps({"event": "step_update", "result": "tool output"}),
            json.dumps({"type": "heartbeat"}),
            result_line(response="# Guide"),
        ]
    )
    assert parse_reply(stdout) == "# Guide"


def test_parse_reply_takes_the_last_result_event() -> None:
    stdout = "\n".join([result_line(response="first"), result_line(response="second")])
    assert parse_reply(stdout) == "second"


@pytest.mark.parametrize(
    ("stdout", "message"),
    [
        (json.dumps({"event": "init"}), "no result event"),
        ("not json\n" + result_line(), r"not a JSON object \(line 1\): not json"),
        (json.dumps(["a", "list"]), "not a JSON object"),
        (json.dumps({"event": "result"}), r"result event without a result \(line 1\)"),
        (json.dumps({"event": "result", "result": "text"}), r"malformed result event \(line 1\)"),
        (json.dumps({"event": "result", "result": {"response": "# Guide"}}), "malformed result event"),
        (result_line(status="ERROR", response="", error="invalid model selection"), "status ERROR: invalid model"),
        (result_line(status="ERROR", response=""), "status ERROR: no error message"),
        (result_line(finished_reason="timeout"), "ended the turn early: timeout"),
        (result_line(response="  \n"), "empty reply"),
    ],
)
def test_parse_reply_errors(stdout: str, message: str) -> None:
    with pytest.raises(GuideError, match=message):
        _ = parse_reply(stdout)


@pytest.mark.parametrize(
    ("fake", "message"),
    [
        (FakeAgy(returncode=1, stderr="error: invalid model selection"), "exited with status 1: error: invalid model"),
        (FakeAgy(stdout="  \n"), "empty reply"),
        (FakeAgy(error=FileNotFoundError("agy")), "Is Antigravity CLI installed"),
        (FakeAgy(error=subprocess.TimeoutExpired(["agy"], 60)), r"\[guide.options.agy-cli\]"),
        (FakeAgy(stdout=result_line(status="ERROR", response="", error="quota")), "status ERROR: quota"),
    ],
)
def test_generate_errors(tmp_path: Path, fake: FakeAgy, message: str) -> None:
    with pytest.raises(GuideError, match=message):
        _ = AgyCliGenerator(AgyCliOptions(), fake).generate("PROMPT", VIDEO, tmp_path)


def fake_agy_command(body: str) -> list[str]:
    """Return a command that runs `body` as a Python script, with `RESULT` holding a successful result line."""
    return [sys.executable, "-c", f"import os, sys, time, signal\nRESULT = {result_line()!r}\n{body}"]


def run_fake_agy(body: str, cwd: Path, timeout: float = 30, prompt: str = "PROMPT") -> subprocess.CompletedProcess[str]:
    return run_agy(fake_agy_command(body), prompt, cwd, timeout, {}, exit_grace=0.3, kill_grace=0.3)


def test_run_agy_sends_the_prompt_from_cwd_with_extra_env(tmp_path: Path) -> None:
    body = "print(sys.stdin.read(), os.getcwd(), os.environ['EXTRA'], 'PATH' in os.environ, sep='|')"
    completed = run_agy(fake_agy_command(body), "PROMPT", tmp_path, 30, {"EXTRA": "x"})
    assert completed.returncode == 0
    assert completed.stdout == f"PROMPT|{tmp_path}|x|True\n"


def test_run_agy_returns_when_agy_exits_by_itself(tmp_path: Path) -> None:
    body = "sys.stdin.read(); print('{\"event\": \"init\"}'); print(RESULT); print('err', file=sys.stderr)"
    completed = run_fake_agy(body, tmp_path)
    assert completed.returncode == 0
    assert completed.stdout == '{"event": "init"}\n' + result_line() + "\n"
    assert completed.stderr == "err\n"


def test_run_agy_handles_a_prompt_larger_than_the_pipe_buffer(tmp_path: Path) -> None:
    completed = run_fake_agy("print(len(sys.stdin.read()))", tmp_path, prompt="x" * 1_000_000)
    assert completed.stdout == "1000000\n"


@pytest.mark.parametrize(
    "ignore_sigterm",
    [pytest.param(False, id="exits-on-sigterm"), pytest.param(True, id="needs-sigkill")],
)
def test_run_agy_stops_agy_when_it_hangs_after_the_result(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, ignore_sigterm: bool
) -> None:
    ignore = "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n" if ignore_sigterm else ""
    body = f"{ignore}sys.stdin.read(); print(RESULT, flush=True); time.sleep(60)"
    start = time.monotonic()
    with caplog.at_level(logging.WARNING, logger=agy_cli.__name__):
        completed = run_fake_agy(body, tmp_path)
    assert time.monotonic() - start < 10
    assert completed.returncode == 0
    assert parse_reply(completed.stdout) == "# Guide\n"
    messages = [record.getMessage() for record in caplog.records]
    assert messages[0] == "agy did not exit within 0.3 seconds after printing its result; stopping it."
    assert messages[1:] == (["agy ignored SIGTERM for 0.3 seconds; killing it."] if ignore_sigterm else [])


def test_run_agy_replaces_bytes_that_are_not_utf8(tmp_path: Path) -> None:
    body = "sys.stdout.buffer.write(b'\\xff caf\\xc3\\xa9\\n'); sys.exit(1)"
    start = time.monotonic()
    completed = run_fake_agy(body, tmp_path)
    assert time.monotonic() - start < 10
    assert (completed.returncode, completed.stdout) == (1, "� café\n")


def test_run_agy_raises_a_reader_error_at_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(line: str) -> bool:
        raise RuntimeError(f"cannot read {line.strip()}")

    monkeypatch.setattr(agy_cli, "is_result_event", broken)
    start = time.monotonic()
    with pytest.raises(RuntimeError, match="cannot read line"):
        _ = run_fake_agy("print('line', flush=True); time.sleep(60)", tmp_path)
    assert time.monotonic() - start < 10


def test_run_agy_keeps_the_exit_status_when_agy_exits_after_the_result(tmp_path: Path) -> None:
    completed = run_fake_agy("print(RESULT, flush=True); sys.exit(2)", tmp_path)
    assert completed.returncode == 2


def test_run_agy_returns_a_failure_without_a_result(tmp_path: Path) -> None:
    completed = run_fake_agy("print('error: quota', file=sys.stderr); sys.exit(3)", tmp_path)
    assert (completed.returncode, completed.stdout, completed.stderr) == (3, "", "error: quota\n")


def test_run_agy_times_out_without_a_result(tmp_path: Path) -> None:
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        _ = run_fake_agy('print(\'{"event": "init"}\', flush=True); time.sleep(60)', tmp_path, timeout=0.5)
    assert time.monotonic() - start < 10


def test_run_agy_reports_a_missing_command(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        _ = run_agy(["no-such-agy-command"], "PROMPT", tmp_path, 5, {})


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (result_line(), True),
        (json.dumps({"event": "result"}), True),
        (json.dumps({"event": "init"}), False),
        ("not json", False),
        (json.dumps(["a", "list"]), False),
        ("", False),
    ],
)
def test_is_result_event(line: str, expected: bool) -> None:
    assert is_result_event(line) is expected
