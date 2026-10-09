import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from youtube_tools.guide import GuideError
from youtube_tools.claude_cli import SYSTEM_PROMPT, ClaudeCliOptions, ClaudeCliGenerator


class FakeClaude:
    def __init__(
        self, stdout: str = "# Guide\n", returncode: int = 0, stderr: str = "", error: Exception | None = None
    ):
        self.calls: list[tuple[list[str], str, Path, float]] = []
        self.result = (stdout, returncode, stderr)
        self.error = error

    def __call__(self, args: list[str], prompt: str, cwd: Path, timeout: float) -> subprocess.CompletedProcess[str]:
        self.calls.append((args, prompt, cwd, timeout))
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
        "--no-session-persistence",
        "--output-format",
        "text",
        "--system-prompt",
        SYSTEM_PROMPT,
    ]


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
    reply = ClaudeCliGenerator(ClaudeCliOptions(timeout_seconds=60), fake).generate("PROMPT", tmp_path)
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
        _ = ClaudeCliGenerator(ClaudeCliOptions(), fake).generate("PROMPT", tmp_path)
