import subprocess
from pathlib import Path

import pytest

from ces_youtube_tools.guide import GuideError
from ces_youtube_tools.generators._subprocess import run_prompt_command


class FakeRunner:
    def __init__(
        self, stdout: str = "# Guide\n", returncode: int = 0, stderr: str = "", error: Exception | None = None
    ):
        self.calls: list[tuple[list[str], str, Path, float, dict[str, str]]] = []
        self.result: tuple[str, int, str] = (stdout, returncode, stderr)
        self.error: Exception | None = error

    def __call__(
        self, args: list[str], prompt: str, cwd: Path, timeout: float, extra_env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((args, prompt, cwd, timeout, extra_env))
        if self.error is not None:
            raise self.error
        stdout, returncode, stderr = self.result
        return subprocess.CompletedProcess(args, returncode, stdout, stderr)


def run(runner: FakeRunner, cwd: Path) -> str:
    return run_prompt_command(
        runner,
        ["npx", "tool", "--print"],
        "PROMPT",
        cwd,
        90,
        {"KEY": "1"},
        display_name="tool",
        install_hint="Is Tool installed?",
        options_table="guide.options.tool-cli",
    )


def test_passes_its_arguments_to_the_runner_and_returns_stdout(tmp_path: Path) -> None:
    runner = FakeRunner(stdout="# Guide\n")
    assert run(runner, tmp_path) == "# Guide\n"
    assert runner.calls == [(["npx", "tool", "--print"], "PROMPT", tmp_path, 90, {"KEY": "1"})]


@pytest.mark.parametrize(
    ("runner", "message"),
    [
        (FakeRunner(error=FileNotFoundError("npx")), "Cannot run 'npx'. Is Tool installed?"),
        (
            FakeRunner(error=subprocess.TimeoutExpired(["npx"], 90)),
            "tool did not finish within 90 seconds. Raise timeout_seconds in [guide.options.tool-cli].",
        ),
        (FakeRunner(returncode=2, stdout="partial", stderr=" Bad flag \n"), "tool exited with status 2: Bad flag"),
        (FakeRunner(returncode=2, stdout="Only stdout\n"), "tool exited with status 2: Only stdout"),
        (FakeRunner(returncode=1, stderr="a" * 500 + "b" * 1000), "tool exited with status 1: " + "b" * 1000),
        (FakeRunner(stdout=" \n"), "tool returned an empty reply."),
    ],
)
def test_failures_raise_guide_error(tmp_path: Path, runner: FakeRunner, message: str) -> None:
    with pytest.raises(GuideError) as excinfo:
        _ = run(runner, tmp_path)
    assert str(excinfo.value) == message
