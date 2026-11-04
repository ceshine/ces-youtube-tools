"""The Typer app: the command tree, and where every command is registered."""

import re
import inspect
from typing import Any, TypeVar
from collections.abc import Callable

import typer

from .list_cmd import list_videos
from .site_cmd import build_site
from .guide_cmd import make_guide
from .browse_cmd import browse
from .config_cmd import (
    config_init,
    config_show,
    config_check,
    config_options,
    config_profile,
    config_template,
    config_add_channel,
    config_remove_channel,
)
from .backfill_cmd import backfill_guides
from .transcript_cmd import show_transcript

APP = typer.Typer(no_args_is_help=True, help="Browse YouTube channels and generate watching guides.")
CONFIG_APP = typer.Typer(no_args_is_help=True, help="Create and edit the configuration file.")
APP.add_typer(CONFIG_APP, name="config")

_DOCSTRING_SECTION = re.compile(r"^(Args|Returns|Raises|Yields|Examples?|Notes?):\s*$")

CommandFunction = TypeVar("CommandFunction", bound=Callable[..., Any])


def help_text(function: Callable[..., Any]) -> str:
    """Get a command's help text: its docstring up to the first Google-style section such as `Args:`.

    Typer shows the whole docstring otherwise, including the argument documentation the code style requires.

    Args:
        function (Callable[..., Any]): The command function.

    Returns:
        str: The docstring's description paragraphs.
    """
    lines: list[str] = []
    for line in inspect.cleandoc(function.__doc__ or "").splitlines():
        if _DOCSTRING_SECTION.match(line.strip()):
            break
        lines.append(line)
    return "\n".join(lines).strip()


def command(app: typer.Typer, name: str) -> Callable[[CommandFunction], CommandFunction]:
    """Register a function as a Typer command, with help text taken from its docstring description.

    Args:
        app (typer.Typer): The app or command group.
        name (str): The command name.

    Returns:
        Callable[[CommandFunction], CommandFunction]: The decorator.
    """

    def register(function: CommandFunction) -> CommandFunction:
        _ = app.command(name, help=help_text(function))(function)
        return function

    return register


@APP.callback()
def main() -> None:
    """Browse YouTube channels and generate watching guides.

    The callback keeps the app a command group, so subcommands are invoked by name.

    Returns:
        None
    """


_ = command(CONFIG_APP, "init")(config_init)
_ = command(CONFIG_APP, "template")(config_template)
_ = command(CONFIG_APP, "profile")(config_profile)
_ = command(CONFIG_APP, "add-channel")(config_add_channel)
_ = command(CONFIG_APP, "remove-channel")(config_remove_channel)
_ = command(CONFIG_APP, "show")(config_show)
_ = command(CONFIG_APP, "options")(config_options)
_ = command(CONFIG_APP, "check")(config_check)

# `--help` lists these commands in this order. Typer lists the `config` group after them.
_ = command(APP, "list")(list_videos)
_ = command(APP, "transcript")(show_transcript)
_ = command(APP, "guide")(make_guide)
_ = command(APP, "backfill")(backfill_guides)
_ = command(APP, "site")(build_site)
_ = command(APP, "browse")(browse)
