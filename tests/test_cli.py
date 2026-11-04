from pathlib import Path

import pytest
from typer.testing import CliRunner

from ces_youtube_tools import cli
from ces_youtube_tools.cli import APP

RUNNER = CliRunner()


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    return tmp_path / "config" / "config.toml"


def test_help_runs() -> None:
    result = RUNNER.invoke(APP, ["--help"])
    assert result.exit_code == 0
    assert "watching guides" in result.output


def test_commands_need_a_config(config_path: Path) -> None:
    result = RUNNER.invoke(APP, ["config", "show", "--config", str(config_path)])
    assert result.exit_code == 1
    assert "config init" in result.output


def test_help_text_stops_at_docstring_sections() -> None:
    def documented() -> None:
        """Do a thing.

        More detail.

        Args:
            x (int): Hidden from help.
        """

    assert cli.help_text(documented) == "Do a thing.\n\nMore detail."
    result = RUNNER.invoke(APP, ["list", "--help"], env={"COLUMNS": "200"})
    assert "Args:" not in result.output
    assert "newest first" in result.output
