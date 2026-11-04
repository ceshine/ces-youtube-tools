"""The `site` command: build the guides into a static site."""

from pathlib import Path
from typing import Annotated

import typer

from .common import fail, STDERR, CONSOLE, ConfigPathOption, load_config_or_exit
from ..config import DEFAULT_CONFIG_PATH
from ..guide_html import expand_paths, GuideHtmlError
from ..static_site import SiteError, write_site, collect_pages


def build_site(
    out_dir: Annotated[
        Path | None,
        typer.Option("--out", "-o", help="Folder to write the site into. Default: the configured site.output_dir."),
    ] = None,
    directory: Annotated[
        Path | None, typer.Argument(help="Folder of guides. Default: the configured guide.output_dir.")
    ] = None,
    include_members_only: Annotated[
        bool,
        typer.Option(
            "--include-members-only",
            help="Also publish members-only guides and guides whose availability is unknown. Only for a site "
            + "behind Cloudflare Access.",
        ),
    ] = False,
    config_path: ConfigPathOption = DEFAULT_CONFIG_PATH,
) -> None:
    """Build the guides into a static site for Cloudflare Pages.

    Writes one page per guide, an index, `robots.txt` and `_headers` into the output folder, which is cleared first
    if an earlier build wrote it. Members-only guides, and guides whose availability is unknown, are left out unless
    `--include-members-only` is given. Deploy with `npx wrangler pages deploy OUT`, to a project behind Cloudflare
    Access.

    Args:
        out_dir (Path | None): Folder to write the site into.
        directory (Path | None): Folder of guides.
        include_members_only (bool): Also publish members-only guides and guides whose availability is unknown.
        config_path (Path): Path of the configuration file.

    Returns:
        None

    Raises:
        typer.Exit: If there is no folder of guides or output folder, a guide cannot be built, or the output folder
            cannot be used.
    """
    if directory is None or out_dir is None:
        config = load_config_or_exit(config_path)
        directory = directory if directory is not None else config.guide.output_dir
        out_dir = out_dir if out_dir is not None else config.site.output_dir
    if directory is None:
        raise fail("No folder given and guide.output_dir is not set. Pass the folder of guides.")
    if out_dir is None:
        raise fail("No --out given and site.output_dir is not set. Pass the output folder with -o.")
    directory = directory.expanduser()
    out_dir = out_dir.expanduser()
    try:
        files = expand_paths([str(directory)], Path.cwd())
        build = collect_pages(files, include_members_only)
        write_site(build, out_dir)
    except (GuideHtmlError, SiteError) as exc:
        raise fail(str(exc)) from exc
    pages = "1 guide page" if len(build.pages) == 1 else f"{len(build.pages)} guide pages"
    CONSOLE.print(f"Wrote {pages} and the index to {out_dir}.", markup=False, highlight=False, soft_wrap=True)
    if build.left_out:
        CONSOLE.print(
            f"Left out {len(build.left_out)} members-only guides or guides whose availability is unknown "
            + "(--include-members-only adds them).",
            markup=False,
            highlight=False,
            soft_wrap=True,
        )
    if include_members_only:
        STDERR.print(
            "[yellow]Warning:[/yellow] members-only guides are included. Deploy this build only to a project "
            + "behind Cloudflare Access.",
            highlight=False,
        )
    CONSOLE.print(f"Deploy with: npx wrangler pages deploy {out_dir}", markup=False, highlight=False, soft_wrap=True)
