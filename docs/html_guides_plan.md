# Plan: HTML watching guides with an embedded player

Status: implemented (steps 1-7 done, 2026-10-09). This file is kept as a record of the steps and of what is still to
verify by hand. Where it and the code differ, the code and [design.md](design.md) win.

## Goal

Turn a Markdown watching guide into a standalone HTML page that embeds the video's player, so that clicking a
timestamp moves the player. The decisions behind it (conversion, page template, player behavior, `html`, `serve` and
`--html`) are recorded in [design.md](design.md), under "HTML pages and `serve`". Observed YouTube behavior is in
[implementation_notes.md](implementation_notes.md).

## Steps

Each step ends with `uvx ruff check .`, `uvx ruff format --line-length 120` and `uv run pytest` passing.

1. **Conversion core.** `uv add markdown-it-py`. Add `markdown_to_html`, `find_video_id`, `guide_title` and tests
   with a short synthetic guide in `tests/fixtures/` (headings, timestamp links, a bullet list, a table, a `<script>`
   line that must come out escaped, and a `javascript:` link that must not become a link).
2. **Page template.** Add `templates/guide_page.html` and `render_guide_page`. Tests: the placeholders are filled,
   the video ID is in the player setup, a title with `<`, `&` or `"` is escaped, and an ID that does not match
   `[A-Za-z0-9_-]{11}` (for example `x"; alert(1); "`) raises `GuideHtmlError`.
3. **Player behavior.** Write the IFrame API setup, the click handler and the hiding of the player. Check it by
   hand in Firefox and Chrome (see "To verify" below), and record what was found in `implementation_notes.md`.
   (Template and checks in Chrome done on 2026-10-09.)
4. **`html` command.** Add `expand_paths`, `write_guide_page` and the Typer command. Tests (in `tmp_path`): a single
   file, a folder (not recursive), other files ignored (an error if nothing else is left), a missing path, an
   unreadable folder, a folder without Markdown files, an existing page with and without `--overwrite`, a file
   without a video ID among valid ones.
5. **`serve` command.** Tests: the handler serves files from the given folder (a server on port 0 in a thread,
   fetched with `urllib`), the server binds to `127.0.0.1`, a request with a foreign `Host` header gets 403, no
   `DIR` and no `guide.output_dir` gives a clear error, a port in use gives a clear error. `webbrowser.open` is
   mocked.
6. **`--html` flag.** Add the flag to `guide` and `browse` and pass it through `_run_guide`. Tests using the
   existing mocked pipeline in `tests/test_cli.py`: the HTML file is written next to the guide; the "kept the
   existing guide" path writes a missing page and keeps an existing one with a warning; `guide --force` replaces
   an existing page.
7. **Docs.** Move the decisions into `design.md` (workflow diagram, a new "HTML page" section), add the command and
   the flag to `README.md`, and delete or shorten this plan.

## To verify (not yet verified)

Record each result in `implementation_notes.md` with the date and a recheck command.

- **Embedding from a `file://` page: confirmed broken** (2026-10-09, see "The embedded player refuses `file://`
  pages" in `implementation_notes.md`). Error 153 in headless Chrome and in the user's browser; served from
  `localhost` it plays. Hence the `serve` command and hiding the player on `file://` pages.
- **Members-only videos: checked** (2026-10-09, Firefox). The player shows the join prompt unless Enhanced Tracking
  Protection is off for the page; no `onError` fires. See the implementation notes. Chrome: not yet verified.
- **Videos with embedding turned off** by the uploader: confirm that `onError` fires (codes 101/150) and the
  player is hidden. (A made-up video ID already gives a code in that group and hides the player.)

## Out of scope for now

- PDF or EPUB output.
- Dark mode (`prefers-color-scheme: dark`): redefine the color variables in `:root`.
- A user-editable page template or CSS file.
- An index page that lists all guides in a folder, to replace the plain folder listing of `serve`.
- Marking the latest timestamp link at or before the player's current time ("now playing"). It works per link, so it
  needs no knowledge of the guide's structure.
- A `[guide] html = true` setting that makes `--html` the default.
- Embedding from `youtube-nocookie.com` (privacy-enhanced mode, no YouTube cookies until playback). Decided against
  as the default on 2026-10-09: members-only videos need the viewer's `youtube.com` sign-in, which a different
  domain would not receive, so they would very likely show the join prompt even with tracking protection off (not
  yet verified). The benefit is small for pages served from `localhost`. If it is wanted later, make it opt-in
  (for example a `--privacy` flag that applies to public videos only), after checking that the IFrame API
  (`onReady`, `seekTo`, `onError`) works against a nocookie `<iframe src>`. Note that the `host` player option does not
  matter here: the page's `<iframe>` already exists, so its `src` sets the domain.
