import json
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from ces_youtube_tools.guide import GuideError
from ces_youtube_tools.video import VideoInfo
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


def test_run_agy_adds_extra_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.update(kwargs)
        return subprocess.CompletedProcess(args, 0, "ok", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    _ = run_agy(["agy"], "PROMPT", tmp_path, 5, {"EXTRA": "x"})
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["EXTRA"] == "x"
    assert "PATH" in env
    assert (captured["input"], captured["cwd"], captured["timeout"]) == ("PROMPT", tmp_path, 5)
