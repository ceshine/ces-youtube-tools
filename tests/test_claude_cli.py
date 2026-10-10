import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from ces_youtube_tools.guide import GuideError
from ces_youtube_tools.video import VideoInfo
from ces_youtube_tools.claude_cli import (
    run_claude,
    SYSTEM_PROMPT,
    ClaudeCliOptions,
    ClaudeCliGenerator,
    DISABLE_CLAUDE_MDS_ENV,
    DISABLE_PROMPT_CACHING_ENV,
)

VIDEO = VideoInfo(id="abc123", title="A video", duration=60)


class FakeClaude:
    def __init__(
        self, stdout: str = "# Guide\n", returncode: int = 0, stderr: str = "", error: Exception | None = None
    ):
        self.calls: list[tuple[list[str], str, Path, float]] = []
        self.envs: list[dict[str, str]] = []
        self.result = (stdout, returncode, stderr)
        self.error = error

    def __call__(
        self, args: list[str], prompt: str, cwd: Path, timeout: float, extra_env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((args, prompt, cwd, timeout))
        self.envs.append(extra_env)
        if self.error is not None:
            raise self.error
        stdout, returncode, stderr = self.result
        return subprocess.CompletedProcess(args, returncode, stdout, stderr)


def test_build_args_defaults() -> None:
    assert ClaudeCliGenerator(ClaudeCliOptions()).build_args() == [
        "claude",
        "--print",
        "--tools",
        "",
        "--output-format",
        "text",
        "--system-prompt",
        SYSTEM_PROMPT,
        "--no-session-persistence",
    ]


def test_build_args_with_session_dir_names_the_session(tmp_path: Path) -> None:
    args = ClaudeCliGenerator(ClaudeCliOptions(session_dir=tmp_path)).build_args("abc123")
    assert "--no-session-persistence" not in args
    assert args[-2:] == ["--name", "abc123"]


def test_build_args_with_session_dir_and_no_video_id(tmp_path: Path) -> None:
    args = ClaudeCliGenerator(ClaudeCliOptions(session_dir=tmp_path)).build_args()
    assert "--no-session-persistence" not in args
    assert "--name" not in args


def test_build_args_with_options() -> None:
    options = ClaudeCliOptions(command=["npx", "claude"], model="opus", effort="high", bare=True, extra_args=["-x"])
    args = ClaudeCliGenerator(options).build_args()
    assert args[:2] == ["npx", "claude"]
    assert args[-6:] == ["--model", "opus", "--effort", "high", "--bare", "-x"]


def test_options_reject_unknown_keys() -> None:
    with pytest.raises(ValidationError):
        _ = ClaudeCliOptions.model_validate({"modle": "opus"})


def test_generate_sends_prompt_on_stdin_from_the_work_dir(tmp_path: Path) -> None:
    fake = FakeClaude(stdout="# Guide\n")
    reply = ClaudeCliGenerator(ClaudeCliOptions(timeout_seconds=60), fake).generate("PROMPT", VIDEO, tmp_path)
    assert reply == "# Guide\n"
    _, prompt, cwd, timeout = fake.calls[0]
    assert (prompt, cwd, timeout) == ("PROMPT", tmp_path, 60)


@pytest.mark.parametrize(
    ("fake", "message"),
    [
        (FakeClaude(returncode=1, stderr="Not logged in"), "exited with status 1: Not logged in"),
        (FakeClaude(stdout="  \n"), "empty reply"),
        (FakeClaude(error=FileNotFoundError("claude")), "Is Claude Code installed"),
        (FakeClaude(error=subprocess.TimeoutExpired(["claude"], 60)), "did not finish within 1800 seconds"),
    ],
)
def test_generate_errors(tmp_path: Path, fake: FakeClaude, message: str) -> None:
    with pytest.raises(GuideError, match=message):
        _ = ClaudeCliGenerator(ClaudeCliOptions(), fake).generate("PROMPT", VIDEO, tmp_path)


def test_generate_runs_in_session_dir_and_creates_it(tmp_path: Path) -> None:
    work_dir = tmp_path / "work"
    session_dir = tmp_path / "sessions" / "claude"
    fake = FakeClaude()
    _ = ClaudeCliGenerator(ClaudeCliOptions(session_dir=session_dir), fake).generate("PROMPT", VIDEO, work_dir)
    args, _, cwd, _ = fake.calls[0]
    assert cwd == session_dir
    assert session_dir.is_dir()
    assert args[-2:] == ["--name", "abc123"]


def test_generate_without_session_dir_runs_in_the_work_dir(tmp_path: Path) -> None:
    fake = FakeClaude()
    _ = ClaudeCliGenerator(ClaudeCliOptions(), fake).generate("PROMPT", VIDEO, tmp_path)
    args, _, cwd, _ = fake.calls[0]
    assert cwd == tmp_path
    assert "--no-session-persistence" in args
    assert "--name" not in args


def test_generate_rejects_a_session_dir_that_is_a_file(tmp_path: Path) -> None:
    session_dir = tmp_path / "not-a-dir"
    session_dir.write_text("x", encoding="utf-8")
    fake = FakeClaude()
    with pytest.raises(GuideError, match="Cannot use session_dir"):
        _ = ClaudeCliGenerator(ClaudeCliOptions(session_dir=session_dir), fake).generate("PROMPT", VIDEO, tmp_path)
    assert fake.calls == []


def test_session_dir_expands_the_home_folder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    options = ClaudeCliOptions.model_validate({"session_dir": "~/sessions"})
    assert options.session_dir == tmp_path / "sessions"


def test_run_claude_disables_claude_md_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.update(kwargs)
        return subprocess.CompletedProcess(args, 0, "ok", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    _ = run_claude(["claude"], "PROMPT", tmp_path, 5, {"EXTRA": "x"})
    env = captured["env"]
    assert isinstance(env, dict)
    assert env[DISABLE_CLAUDE_MDS_ENV] == "1"
    assert env["EXTRA"] == "x"
    assert "PATH" in env


def test_generate_disables_prompt_caching_by_default(tmp_path: Path) -> None:
    fake = FakeClaude()
    _ = ClaudeCliGenerator(ClaudeCliOptions(), fake).generate("PROMPT", VIDEO, tmp_path)
    assert fake.envs == [{DISABLE_PROMPT_CACHING_ENV: "1"}]


def test_generate_keeps_prompt_caching_when_enabled(tmp_path: Path) -> None:
    fake = FakeClaude()
    _ = ClaudeCliGenerator(ClaudeCliOptions(prompt_caching=True), fake).generate("PROMPT", VIDEO, tmp_path)
    assert fake.envs == [{}]
