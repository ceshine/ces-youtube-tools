# Plan: HTML watching guides with an embedded player

Status: planned, not started. When this is implemented, move the decisions into [design.md](design.md) and keep this
file only as a record of the steps.

## Goal

Turn a Markdown watching guide into a standalone HTML page. The page embeds the video's YouTube player, and clicking
a timestamp link moves that player to that moment instead of opening YouTube. Reading the guide and watching the
video then happen on one page.

- `youtube-tools html PATH...` converts existing guides. A path can be a file or a folder.
- `youtube-tools guide --html` and `youtube-tools browse --html` write the HTML page next to the new Markdown guide.
- `youtube-tools serve [DIR]` serves a folder of pages on `localhost`, because YouTube's embedded player does not
  work on a page opened from disk.

## Decisions

### Markdown conversion: `markdown-it-py`

- It is already in `uv.lock` (version 4.2.0) because `rich` depends on it. Add it as a direct dependency with
  `uv add markdown-it-py`, because the code imports it and should not rely on another package's dependencies.
- Use `MarkdownIt("commonmark", {"html": False, "typographer": True}).enable(["table", "replacements", "smartquotes"])`:
  - `"html": False` is required. The `commonmark` preset passes raw HTML through by default, and a guide is written
    by a model from a transcript, so a `<script>` in it must come out as text.
  - `enable("table")` turns on GFM tables, which the prompt asks for when a video compares items.
  - markdown-it already rejects `javascript:` and similar link URLs (`validateLink`).
  - `typographer` with `smartquotes` and `replacements` turns `"quotes"` into curly quotes and `--` into an en dash.
    Guides quote the video often, so this is a cheap readability gain.
- Pandoc (an external binary, not installed here) and rendering in the browser (needs a network connection and
  JavaScript to show anything) were considered and rejected.

### Page template

- A new packaged file, `src/youtube_tools/templates/guide_page.html`, filled with `string.Template`, like the prompt
  template. Placeholders: `$title`, `$video_id`, `$body`. Any `$` in its CSS or JavaScript must be written as `$$`.
  Write the JavaScript without `$` so this never comes up.
- The CSS and JavaScript are inline, so one `.html` file is the whole page. The only external resources are the
  YouTube player and its IFrame API script, which need a network connection anyway.
- The template is not user-editable in the first version (no config key). It can follow the `prompt_template`
  pattern later if needed.
- Minimal parsing: the page relies only on timestamp links, which `check_timestamp_links` already checks. It does
  not depend on headings, range lines or any other part of the guide's structure, because a model's output (or an
  edited prompt template, or a guide written by hand) may not follow the prompt exactly. The rendered Markdown is
  styled with plain element styles (`h2`, `ul`, `table`, ...), so any valid Markdown looks fine. No segment list,
  no wrapping of segments in `<section>` elements.
- Layout, the same on every screen size:
  - The player is at the top of the page (16:9, centered, limited width) and scrolls away with the page, so the
    guide gets the whole window when it is read in full.
  - Below the player, a toggle: "Scroll to the video when a timestamp is clicked" (on by default). The setting is
    kept in `localStorage`, inside `try`/`catch`, because some browsers block storage for `file://` pages. The
    toggle is hidden when JavaScript is off.
  - The guide below, at most about 70 characters per line.
  - Timestamp links are styled as small pills with fixed-width digits, picked out by a CSS attribute selector on
    `t=` in the link, so no parsing is needed for this either.
  - Fonts: Literata (body) and Inter (headings, timestamps, controls) from Google Fonts with `display=swap`, so
    the page falls back to system fonts at once when offline. Inter's tabular figures line up the timestamps, so
    no monospace font is needed. (Chosen over Source Serif 4 + Source Sans 3 on 2026-10-09.)
  - Style hints, set as classes by a short script that looks only at how an element starts (CSS `:first-child`
    cannot see text before the first element). Without the script, or when a guide is written differently, the
    elements keep the plain styles:
    - a list item that starts with a timestamp link (`li.stamped`): a timeline row with the time in an aligned
      column and the `: ` after it removed; its list gets a thin line on the left instead of bullets;
    - a paragraph that starts with a timestamp link right after a heading (`p.range`): small muted text;
    - a paragraph that starts with bold text (`p.labeled`), like "How to use this guide:": a tinted callout.
  - Hover: a timestamp pill fills with the accent color, and a timeline row gets a faint background. Tables have
    a bold header row with a heavier bottom border, and striped rows.
  - A light theme only (dark mode is out of scope for now). Readable tables with borders and horizontal scrolling when
    they are too wide. A print stylesheet hides the player and the toggle.
- `<title>` comes from the guide's first `# ` heading (without the "Watching Guide: " prefix), else the file name.

### Embedded player

- The player is a plain `<iframe src="https://www.youtube.com/embed/<id>?enablejsapi=1">` in the HTML, and the
  IFrame API (`https://www.youtube.com/iframe_api`) attaches a `YT.Player` to it. The video is then visible even
  without JavaScript or when the API script fails to load.
- One click handler on the page catches clicks on links to `youtube.com/watch?v=<same id>&t=<n>s` (and
  `youtu.be/<id>?t=`). It calls `player.seekTo(n, true)` and `player.playVideo()`, and prevents the default action.
  When the toggle is on, it then scrolls the player into view (smoothly, unless `prefers-reduced-motion` is set).
  The clicked link flashes briefly, so the reader can find their place again after scrolling back.
- Links keep their normal behavior when:
  - the click is a middle click or uses Ctrl, Cmd or Shift (open in a new tab),
  - the link points to another video,
  - the player is not ready, is hidden, or reported an error.
- The player is hidden, and a note at the top says why and that timestamps open YouTube, when:
  - the page is opened from disk (`location.protocol === "file:"`). YouTube refuses such pages with Error 153, so a
    small script in `<head>` hides the player before the page is drawn, and the `<iframe>` is removed so it stops
    loading. The note gives the command to run: `youtube-tools serve "<folder of this page>"`, with the folder taken
    from the page's URL (inserted with `textContent`, not as HTML).
  - the player's `onError` fires. Code 153: the same note about `serve`. Codes 100, 101 and 150: the video is
    private, removed, or not allowed on other sites. Any other code is shown as a number.
- If only the IFrame API script fails to load, the player stays visible (it may still play) and the note says that
  timestamps open YouTube.
- Without JavaScript, the page is a readable guide whose links open YouTube, the same as the Markdown file.
- Later, optionally: mark the latest timestamp link at or before the player's current time ("now playing"). It
  works per link, so it needs no knowledge of segments.

### `youtube-tools serve` command

```
youtube-tools serve [DIR] [--port 8765] [--no-open]
```

- Serves `DIR` (default: `guide.output_dir`) with `http.server`'s `ThreadingHTTPServer` and
  `SimpleHTTPRequestHandler(directory=DIR)`, bound to `127.0.0.1` only, so the guides are not reachable from other
  machines. With no `DIR` and no `guide.output_dir`, fail with a message asking for `DIR`. The current directory is
  not a default, because it may hold browser cookie copies or `claude` session logs.
- Requests whose `Host` header is not `127.0.0.1:<port>` or `localhost:<port>` get a 403, so a web page that
  points its own domain name at `127.0.0.1` (DNS rebinding) cannot read the folder.
- Prints the address and opens it in the browser with `webbrowser.open`, unless `--no-open`. The folder listing from
  `SimpleHTTPRequestHandler` is the index for now.
- A fixed default port, not a random free one: `localStorage` is kept per origin, and the origin includes the port,
  so a new port each time would forget the scroll toggle setting. If the port is in use, fail with a message that
  suggests `--port`.
- Runs until Ctrl+C, then exits with status 0. Request logs go to the module logger at debug level, not stderr.

### Finding the video ID

- `guide --html` / `browse --html`: the caller already has the `VideoInfo` and passes `info.id`.
- `html PATH...`: read the ID from the guide's links. Collect the `v=` value of every YouTube link and use the most
  common one. If no link has an ID, report it for that file and skip the file; the other files are still converted.
- The ID must match `[A-Za-z0-9_-]{11}` before it goes into the page. `render_guide_page` checks this itself and
  raises `GuideHtmlError` otherwise, whichever caller passed the ID. The template puts the ID in a JavaScript string
  and in a URL without escaping it, so this check is what keeps the page safe.

### `youtube-tools html` command

```
youtube-tools html PATH... [--overwrite]
```

- Each `PATH` is a Markdown file or a folder. A folder stands for the `.md` files directly inside it (not in its
  subfolders). Files that are not `.md` are ignored completely (no warning, not counted). Duplicates are converted
  once. A path that does not exist, a folder that cannot be read or has no `.md` file, or a run where no `.md` file is
  left at all (for example `html notes.txt`) is an error, and nothing is written.
- There is no wildcard handling in the command: the shell expands `*.md` itself, and folders cover the common case.
- Output: `<same name>.html` in the same folder as each Markdown file. There is no `--output-dir`, so two guides can
  never write the same page, and the pages stay next to the guides that `serve` lists.
- An existing HTML file is kept: the guide is skipped with a warning that names the page and suggests
  `--overwrite`. This is not a failure (exit status stays 0). `--overwrite` replaces existing pages. The page is
  generated from the Markdown file and holds no edits of its own, but the default protects a page the user may have
  changed by hand.
- Prints one line per written file, then a summary (`3 written, 1 skipped`). Skipped counts both kept pages and guides
  that cannot be converted. A guide that cannot be converted (for example, no video ID) is reported and makes the
  exit status 1.

### `--html` flag on `guide` and `browse`

- `--html` writes `Watching Guide <title>.html` next to the Markdown guide after it is written.
- It is passed through `_run_guide`. The HTML is built from the Markdown after `clean_output` and
  `check_timestamp_links`, so its link labels are the corrected ones.
- If the HTML step fails, the Markdown guide is already written. Report the error and exit with status 1, but do
  not delete the Markdown file.
- An existing page follows the guide: when the Markdown guide is written (a new one, `guide --force`, or an
  overwrite confirmed in `browse`), the page is replaced too, so it never lags behind the guide. Neither command
  gets an `--overwrite` option for this.
- When `browse` keeps an existing guide ("Kept the existing guide."), `--html` still writes the HTML page from that
  existing file, so the flag can add a page to a guide written earlier. Here an existing page is kept, with the same
  warning as `youtube-tools html`, because the guide did not change; `youtube-tools html --overwrite` regenerates it.
- A config setting (`[guide] html = true`) would make the flag the default. Leave it out of the first version.

## Module layout

New module `src/youtube_tools/guide_html.py` (not `html.py`, which would shadow the standard library's `html` in
some imports and in reading):

```python
def markdown_to_html(markdown: str) -> str: ...  # body only, raw HTML disabled
def find_video_id(markdown: str) -> str | None: ...  # most common v= in YouTube links
def guide_title(markdown: str, fallback: str) -> str: ...
def render_guide_page(markdown: str, video_id: str, fallback_title: str) -> str: ...  # full page
def write_guide_page(markdown_path: Path, video_id: str | None = None) -> Path: ...  # next to the guide
def expand_paths(paths: list[str], cwd: Path) -> list[Path]: ...  # files and folders
```

`write_guide_page` raises a `GuideHtmlError` (with the file path in its message) when no video ID is found. The CLI
turns that into a skip for `html` and an error for `guide --html`.

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
- **Members-only videos.** Check whether the embedded player plays a members-only video for a signed-in member, or
  shows an error. If it errors, `onError` makes the links open YouTube, which works with the browser's sign-in.
- **`youtube-nocookie.com`.** Check whether `host: "https://www.youtube-nocookie.com"` works with the IFrame API
  and with members-only videos. Use it if it does, since it sets no cookies until playback.
- **Videos with embedding turned off** by the uploader: confirm that `onError` fires (codes 101/150) and the
  player is hidden. (A made-up video ID already gives a code in that group and hides the player.)

## Out of scope for now

- PDF or EPUB output.
- Dark mode (`prefers-color-scheme: dark`): redefine the color variables in `:root`.
- A user-editable page template or CSS file.
- An index page that lists all guides in a folder, to replace the plain folder listing of `serve`.
