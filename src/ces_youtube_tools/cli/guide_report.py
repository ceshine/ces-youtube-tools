"""What `guide` and `browse` print around writing a guide: the template notice, scan warnings and the result."""

from rich.markup import escape

from .common import STDERR, CONSOLE
from ..config import AppConfig
from ..pipeline import GuideTools, GuideResult, PreparedGuide, make_guide_tools
from ..formatting import format_duration


def report_guide(result: GuideResult) -> None:
    """Print where a guide was written, the older guides it replaced, and what the link check found.

    Args:
        result (GuideResult): The written guide.

    Returns:
        None
    """
    links = result.links
    CONSOLE.print(f"Wrote {result.path}", markup=False, highlight=False)
    for old in result.removed:
        CONSOLE.print(f"Removed the older guide {old}", markup=False, highlight=False)
    CONSOLE.print(f"{links.links} timestamp links, {links.relabeled} labels corrected.")
    if links.out_of_range:
        times = ", ".join(format_duration(seconds) for seconds in links.out_of_range)
        STDERR.print(f"[yellow]Warning:[/yellow] links past the end of the video: {times}. Check those parts.")
    if links.links == 0:
        STDERR.print("[yellow]Warning:[/yellow] the guide has no timestamp links. Check the prompt template.")
    if result.raw_reply_error is not None:
        STDERR.print(
            f"[yellow]Warning:[/yellow] the raw reply was not kept in the cache: {escape(result.raw_reply_error)}",
            highlight=False,
            soft_wrap=True,
        )


def print_scan_warnings(prepared: PreparedGuide) -> None:
    """Print a warning for each file in the guide's folder that could not be read or whose front matter is invalid.

    Args:
        prepared (PreparedGuide): The video, from `prepare_guide`.

    Returns:
        None
    """
    for warning in prepared.scan.warnings:
        STDERR.print(f"[yellow]Warning:[/yellow] {escape(warning)}", highlight=False, soft_wrap=True)


def make_guide_tools_with_notice(config: AppConfig) -> GuideTools:
    """Make the tools for writing guides, and print a notice when the built-in prompt template is used.

    Args:
        config (AppConfig): The configuration.

    Returns:
        GuideTools: The tools.

    Raises:
        ValueError: If the configured transcript provider or guide generator is unknown, or the generator's settings
            are invalid.
    """
    tools = make_guide_tools(config)
    if tools.template_is_default:
        STDERR.print(
            f"Using the built-in prompt template ({config.guide.prompt_template} does not exist). "
            "Run `ces-youtube-tools config template` for an editable copy.",
            markup=False,
            highlight=False,
        )
    return tools
