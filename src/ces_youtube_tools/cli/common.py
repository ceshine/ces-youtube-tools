"""What every command shares: the consoles, the `--config` option, and error reporting."""

from pathlib import Path
from typing import Annotated

import typer
from rich.markup import escape
from rich.console import Console

from ..config import AppConfig, ConfigError, load_config

CONSOLE = Console()
# Status and headers for commands whose stdout is data meant for piping.
STDERR = Console(stderr=True)

ConfigPathOption = Annotated[Path, typer.Option("--config", help="Path of the configuration file.")]


def print_error(message: str) -> None:
    """Print an error message.

    Args:
        message (str): The error message, as plain text. Square brackets in it are printed as they are, not read as
            Rich markup, because messages often hold paths, video titles and yt-dlp output such as `[youtube]`.

    Returns:
        None
    """
    STDERR.print(f"[red]Error:[/red] {escape(message)}", markup=True, highlight=False)


def fail(message: str) -> typer.Exit:
    """Print an error message and build the exit to raise.

    Args:
        message (str): The error message, as plain text, as for `print_error`.

    Returns:
        typer.Exit: An exit with status 1, for the caller to raise.
    """
    print_error(message)
    return typer.Exit(code=1)


def load_config_or_exit(config_path: Path) -> AppConfig:
    """Load the configuration or exit with a readable error.

    Args:
        config_path (Path): Path of the configuration file.

    Returns:
        AppConfig: The loaded configuration.

    Raises:
        typer.Exit: If the configuration cannot be loaded.
    """
    try:
        return load_config(config_path)
    except ConfigError as exc:
        raise fail(str(exc)) from exc
