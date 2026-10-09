# Plan: HTML watching guides with an embedded player

Status: planned, not started. When this is implemented, move the decisions into [design.md](design.md) and keep this
file only as a record of the steps.

## Goal

Turn a Markdown watching guide into a standalone HTML page. The page embeds the video's YouTube player, and clicking
a timestamp link moves that player to that moment instead of opening YouTube. Reading the guide and watching the
video then happen on one page.

- `youtube-tools html PATH...` converts existing guides. A path can be a file or a wildcard pattern.
- `youtube-tools guide --html` and `youtube-tools browse --html` write the HTML page next to the new Markdown guide.

## Decisions

### Markdown conversion: `markdown-it-py`

- It is already in `uv.lock` (version 4.2.0) because `rich` depends on it. Add it as a direct dependency with
  `uv add markdown-it-py`, because the code imports it and should not rely on another package's dependencies.
- Use `MarkdownIt("commonmark", {"html": False}).enable("table")`:
  - `"html": False` is required. The `commonmark` preset passes raw HTML through by default, and a guide is written
    by a model from a transcript, so a `<script>` in it must come out as text.
  - `enable("table")` turns on GFM tables, which the prompt asks for when a video compares items.
  - markdown-it already rejects `javascript:` and similar link URLs (`validateLink`).
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
- Layout:
  - Wide screens: two columns, with the player in a sticky column next to the scrolling guide.
  - Narrow screens: the player is sticky at the top at 16:9, and the guide scrolls below it.
  - Light and dark themes through `prefers-color-scheme`. Readable tables with borders and horizontal scrolling when
    they are too wide.
- `<title>` comes from the guide's first `# ` heading (without the "Watching Guide: " prefix), else the file name.

### Embedded player

- Load the IFrame API (`https://www.youtube.com/iframe_api`) and create a `YT.Player` for the video ID.
- One click handler on the guide body catches clicks on links to `youtube.com/watch?v=<same id>&t=<n>s` (and
  `youtu.be/<id>?t=`). It calls `player.seekTo(n, true)` and `player.playVideo()`, and prevents the default action.
- Links keep their normal behavior when:
  - the click is a middle click or uses Ctrl, Cmd or Shift (open in a new tab),
  - the link points to another video,
  - the player is not ready or reported an error (`onError`). In that case a short note above the guide says the
    player is unavailable and that links open YouTube.
- Without JavaScript, the page is a readable guide whose links open YouTube, the same as the Markdown file.
- Mark the segment being played: highlight the segment whose range contains the player's current time. This is
  optional and can wait until after the first version.

### Finding the video ID

- `guide --html` / `browse --html`: the caller already has the `VideoInfo` and passes `info.id`.
- `html PATH...`: read the ID from the guide's links. Collect the `v=` value of every YouTube link and use the most
  common one. If no link has an ID, report it for that file and skip the file; the other files are still converted.
- The ID must match `[A-Za-z0-9_-]{11}` before it goes into the page.

### `youtube-tools html` command

```
youtube-tools html PATH... [-o DIR]
```

- Each `PATH` is a file or a wildcard pattern (`*`, `?`, `[...]`, `**` for subfolders). The shell usually expands
  unquoted patterns, but a quoted pattern (`"guides/**/*.md"`) is expanded by the command. This also covers fish,
  which fails on an unquoted pattern with no matches before the command runs.
- Expansion uses `pathlib`: split the pattern at its first wildcard part into a base directory and the rest, then
  `base.glob(rest)`. (`Path.glob` does not accept absolute patterns, so the split also handles `/abs/path/*.md`.)
- A pattern with no matches is an error that names the pattern. Duplicate matches are converted once. Non-`.md`
  files are skipped with a warning.
- Output: `<same name>.html` next to each Markdown file, or in `--output-dir`.
- An existing HTML file is overwritten. It is generated from the Markdown file and holds no edits of its own, so
  there is no `--force`.
- Prints one line per written file, then a summary (`3 written, 1 skipped`). Exit status 1 if any file failed.

### `--html` flag on `guide` and `browse`

- `--html` writes `Watching Guide <title>.html` next to the Markdown guide after it is written.
- It is passed through `_run_guide`. The HTML is built from the Markdown after `clean_output` and
  `check_timestamp_links`, so its link labels are the corrected ones.
- If the HTML step fails, the Markdown guide is already written. Report the error and exit with status 1, but do
  not delete the Markdown file.
- When `browse` keeps an existing guide ("Kept the existing guide."), `--html` still writes the HTML page from that
  existing file. That way the flag can add a page to a guide written earlier.
- A config setting (`[guide] html = true`) would make the flag the default. Leave it out of the first version.

## Module layout

New module `src/youtube_tools/guide_html.py` (not `html.py`, which would shadow the standard library's `html` in
some imports and in reading):

```python
def markdown_to_html(markdown: str) -> str: ...                        # body only, raw HTML disabled
def find_video_id(markdown: str) -> str | None: ...                     # most common v= in YouTube links
def guide_title(markdown: str, fallback: str) -> str: ...
def render_guide_page(markdown: str, video_id: str, fallback_title: str) -> str: ...  # full page
def write_guide_page(markdown_path: Path, output_dir: Path | None, video_id: str | None = None) -> Path: ...
def expand_paths(patterns: list[str], cwd: Path) -> list[Path]: ...     # files and wildcard patterns
```

`write_guide_page` raises a `GuideHtmlError` (with the file path in its message) when no video ID is found. The CLI
turns that into a skip for `html` and an error for `guide --html`.

## Steps

Each step ends with `uvx ruff check .`, `uvx ruff format --line-length 120` and `uv run pytest` passing.

1. **Conversion core.** `uv add markdown-it-py`. Add `markdown_to_html`, `find_video_id`, `guide_title` and tests
   with a short synthetic guide in `tests/fixtures/` (headings, timestamp links, a bullet list, a table, a `<script>`
   line that must come out escaped, and a `javascript:` link that must not become a link).
2. **Page template.** Add `templates/guide_page.html` and `render_guide_page`. Tests: the placeholders are filled,
   the video ID is in the player setup, and a title with `<`, `&` or `"` is escaped.
3. **Player behavior.** Write the IFrame API setup and the click handler. Check it by hand in Firefox and Chrome
   (see "To verify" below), and record what was found in `implementation_notes.md`.
4. **`html` command.** Add `expand_paths`, `write_guide_page` and the Typer command. Tests (in `tmp_path`): a single
   file, a quoted `*` pattern, a `**` pattern, a pattern with no matches, a file without a video ID among valid
   ones, `--output-dir`.
5. **`--html` flag.** Add the flag to `guide` and `browse` and pass it through `_run_guide`. Tests using the
   existing mocked pipeline in `tests/test_cli.py`: the HTML file is written next to the guide; the "kept the
   existing guide" path still writes it.
6. **Docs.** Move the decisions into `design.md` (workflow diagram, a new "HTML page" section), add the command and
   the flag to `README.md`, and delete or shorten this plan.

## To verify (not yet verified)

Record each result in `implementation_notes.md` with the date and a recheck command.

- **Embedding from a `file://` page.** Since 2025, YouTube's embedded player may refuse to play without an HTTP
  `Referer` and show "Error 153 (video player configuration error)". A page opened from disk sends none. Check by
  opening a generated page directly in Firefox and Chrome. If it fails:
  - add `referrerpolicy="strict-origin-when-cross-origin"` and the `origin` player parameter, and check again;
  - if it still fails, add `youtube-tools html --serve`, which serves the output folder on `localhost` with
    `http.server` and opens the page. The click handler's fallback (links open YouTube) keeps the page usable in
    the meantime.
- **Members-only videos.** Check whether the embedded player plays a members-only video for a signed-in member, or
  shows an error. If it errors, `onError` makes the links open YouTube, which works with the browser's sign-in.
- **`youtube-nocookie.com`.** Check whether `host: "https://www.youtube-nocookie.com"` works with the IFrame API
  and with members-only videos. Use it if it does, since it sets no cookies until playback.
- **Videos with embedding turned off** by the uploader: confirm that `onError` fires (codes 101/150) and the
  fallback note appears.

## Out of scope for now

- PDF or EPUB output.
- A user-editable page template or CSS file.
- An index page that lists all guides in a folder (a natural next step for the `html` command with a wildcard).
