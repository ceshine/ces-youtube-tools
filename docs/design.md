# ces-youtube-tools design

Browse the latest videos of a YouTube channel (including members-only videos when the user is a member), pick one,
and generate a Markdown watching guide from its transcript.

Observed behavior of YouTube, yt-dlp, Firefox and the `claude` CLI, with recheck commands, is in
[implementation_notes.md](implementation_notes.md). Speech recognition options for the future audio transcript
provider are surveyed in [asr_options.md](asr_options.md).

## Workflow

```
 ces-youtube-tools config ...          (run once, edit occasionally)
   ├─ pick a Firefox profile  ──┐
   └─ add/remove channels     ──┴──► ~/.config/ces-youtube-tools/config.toml

 ces-youtube-tools browse [CHANNEL]    (everyday command)
   1. pick a channel from the config
   2. list its latest videos (yt-dlp, no cookies needed) ─► pick one
   3. run the guide pipeline

 ces-youtube-tools guide URL           (same pipeline, no browsing)

 guide pipeline
   VideoInfo ─► TranscriptProvider ─► Transcript ─► GuideGenerator ─► Markdown file
                 · yt-dlp-subs (v1)                  · claude-cli (v1)
                 · audio transcription (later)       · openai-api, other CLIs (later)

 HTML page (optional)
   Markdown file ─► guide_html ─► "<same name>.html" (embedded player) ─► ces-youtube-tools serve ─► browser
                    · `guide --html`, `browse --html`: right after the guide is written
                    · `html PATH...`: guides written earlier
```

## Authentication

YouTube cookies are read live from a dedicated Firefox profile with
`yt-dlp --cookies-from-browser "firefox:<profile path>"`.

Cookies are only needed to fetch subtitles (or audio) of members-only videos. Members-only videos are listed publicly,
so listing runs without cookies, which keeps automated traffic tied to the account low. The transcript provider always
passes cookies, because a video's availability is not always known in advance (see the livestream note in
[implementation_notes.md](implementation_notes.md)).

- A dedicated profile keeps one Google account signed in. Account switching in the main browser profile (Google's
  account menu) does not affect it. A cookie snapshot taken while switching accounts would contain every signed-in
  account.
- The config stores the profile **folder path**, not its display name. Display names live in Firefox's
  `Profile Groups/*.sqlite` database and may disagree with `profiles.ini`. yt-dlp only accepts a path.
- Sign-in check: the profile has the `SAPISID` and `LOGIN_INFO` cookies for `.youtube.com`. A live check fetches
  `https://www.youtube.com/feed/subscriptions`, which needs a login.
- Verified on 2026-10-09: members-only subtitles download with a member's cookies and fail without them.

## Configuration

`~/.config/ces-youtube-tools/config.toml`, read with `tomllib`, written with `tomli-w`, and validated with Pydantic.

```toml
ytdlp_command = ["uvx", "--from", "yt-dlp[default,curl-cffi]@latest", "yt-dlp"]
cache_dir = "~/.cache/ces-youtube-tools"   # per-video work directories

[auth]
firefox_profile = "/home/<user>/.mozilla/firefox/xxxxxxxx.Profile 1"

[[channels]]
name = "3Blue1Brown"
url = "https://www.youtube.com/@3blue1brown"
channel_id = "UCYO_jab_esuFRV4b17AJtAw"
members = false

[transcript]
provider = "yt-dlp-subs"
languages = ["en-orig", "en"]

[guide]
generator = "claude-cli"
prompt_template = "~/.config/ces-youtube-tools/guide_prompt.md"
# output_dir defaults to the directory the command is run from

[guide.options.claude-cli]   # per-generator settings, keyed by generator name
model = "opus"            # optional; default: Claude Code's default model
effort = "high"           # optional
session_dir = "~/.cache/ces-youtube-tools/claude-sessions"   # optional; unset = no session log is kept
# command = ["claude"], bare = false, prompt_caching = false, timeout_seconds = 1800, extra_args = []
```

- `ytdlp_command` defaults to `uvx ... @latest` because YouTube changes often break older yt-dlp releases.
- `channel_id` is resolved once by `config add-channel`. It is the stable key for a channel (handles can change) and
  gives the `UUMO<id without "UC">` members-only playlist if that is ever needed.
- `members` decides whether the listing includes members-only uploads (the `UUMF` and `UUMV` playlists). Leave it
  off for channels you are not a member of, because their members-only videos cannot be downloaded.
- `prompt_template` is a user-editable file. A default template ships with the package
  (`src/ces_youtube_tools/templates/guide_prompt.md`). `config init` writes it next to the config file, and
  `config template [--force]` writes it to the configured path later. If the file is missing, `guide` uses the
  built-in template and says so.
- `config options` lists every option with its type, default and meaning. It reads the Pydantic models (field
  descriptions), including `[guide.options.claude-cli]`, so it works without a config file and cannot drift from the
  code. A new option needs a `Field(description=...)`, or its description column is blank. A new generator's options
  must be added to the command by hand, because `guide.options` is a free-form dict.

## Pipeline interfaces

```python
class TranscriptProvider(Protocol):
    def fetch(self, video: VideoInfo, work_dir: Path) -> Transcript: ...


class GuideGenerator(Protocol):
    def generate(self, prompt: str, video: VideoInfo, work_dir: Path) -> str: ...  # Markdown
```

- `Transcript` is a list of timestamped segments (`start`, `end`, `text`). The yt-dlp provider removes the rolling
  duplicate lines from YouTube's auto-generated VTT subtitles. Generators never see raw subtitle files.
- Prompt building is shared (`guide.render_prompt`): the template plus `VideoInfo` and `Transcript` produce the
  prompt. Generators only send the prompt and return the reply.
- Implementations are chosen by name from a plain dict registry keyed by the config values.

### claude-cli generator

```
# session_dir not set (default): no session log
cd <work dir> && CLAUDE_CODE_DISABLE_CLAUDE_MDS=1 claude --print --tools "" --no-session-persistence \
  --output-format text --system-prompt "<short guide-writer prompt>" [--model M] [--effort E] [--bare] \
  [extra_args] < prompt

# session_dir set: the session log is kept, to track usage
cd <session_dir> && CLAUDE_CODE_DISABLE_CLAUDE_MDS=1 claude --print --tools "" --name <video id> \
  --output-format text --system-prompt "<short guide-writer prompt>" [--model M] [--effort E] [--bare] \
  [extra_args] < prompt
```

- `--tools ""` disables all tools. The model only writes text.
- The prompt goes in on stdin, because transcripts are too long for a command-line argument.
- `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1` keeps every `CLAUDE.md` file out of the run, whatever the working directory is.
  `--system-prompt` replaces Claude Code's coding-agent prompt.
- `DISABLE_PROMPT_CACHING=1` is set unless `prompt_caching = true`. Each run is one request that is never repeated,
  so a cache write only adds cost. (Checked, see `docs/implementation_notes.md`.)
- `--bare` would also skip hooks, plugins and all CLAUDE.md discovery, but it only works with `ANTHROPIC_API_KEY`
  authentication, so it is opt-in (`bare = true`). User-level settings (`~/.claude`) still apply without it.
- `session_dir` (optional, `~` is expanded) turns on session logs for usage tracking. Claude Code stores a log in
  `~/.claude/projects/<working directory, encoded>/`, so the process runs in `session_dir` and all logs land in one
  folder instead of one per video. Without it, `--no-session-persistence` is passed and the process runs in the
  video's work directory, as before.
- `--name <video id>` labels each logged session, using `video.id` from the `generate` arguments. It is not passed
  when `session_dir` is unset, because nothing is stored.
- `CLAUDE_CONFIG_DIR` could move the logs elsewhere, but it moves credentials and settings too, which breaks a
  subscription login. That is why the working directory is used instead.

## Guide step

`ces-youtube-tools guide VIDEO [-o DIR] [--force] [--refresh]` runs the transcript step, then:

1. **Prompt** (`render_prompt`): the template is filled with `string.Template` placeholders (`$title`, `$channel`,
   `$url`, `$video_id`, `$duration`, `$upload_date`, `$today`, `$description`, `$chapters`, `$transcript_note`,
   `$transcript`; `$$` is a literal `$`). `string.Template` instead of `str.format`, because Markdown and JSON in a
   template are full of braces. Unknown placeholders fail before any generation.
2. **Transcript blocks**: segments are joined into blocks of about 15 seconds, each starting with its time in both
   forms, `[1:07 | 67s]`. Fewer, longer blocks save tokens; 30-second blocks made every highlight land on a
   30-second mark, coarser than the hand-written sample guide.
3. **Generation**: the configured generator writes the guide. The prompt and the raw reply are saved in the work
   directory (`prompt.md`, `guide.md`).
4. **Checks** (`check_timestamp_links`, `clean_output`): every timestamp link's label is rewritten from its `t=`
   seconds, which come straight from the block markers; links past the video's end are reported. A code fence
   around the whole reply is removed.
5. **Output**: `Watching Guide <title>.md` in `--output-dir`, else `guide.output_dir`, else the current directory.
   An existing file is refused before generating (no wasted generation) unless `--force`.

The default template asks for the structure of the hand-written sample guide: title, date and channel line, overview
paragraph, "Watch the full video" and "How to use this guide" lines, then numbered segments with a linked range, a
summary and two to eight timestamped highlights, and tables where the video compares items.

## Work directory

`<cache_dir>/<video_id>/` (default `~/.cache/ces-youtube-tools/`) holds:

- `video.json`: the `VideoInfo` fields (title, channel, chapters, available subtitle tracks, ...). Never yt-dlp's raw
  output, which is large and holds signed media URLs.
- `subtitles/`: the downloaded subtitle file.
- `transcript.json`: the `Transcript`.

A rerun with a different generator reuses them, and a failed step is the only one repeated. `--refresh` ignores the
cache. An unreadable cache file is logged and rebuilt.

## Transcript step

`ces-youtube-tools transcript VIDEO` prints a video's transcript (timestamped lines, or `--json`) to stdout, with status
and errors on stderr.

1. `load_video_info`: one full extraction (`yt-dlp -J`, with cookies, because members-only videos need them) gives
   the `VideoInfo`.
2. `choose_track`: human-made subtitles first, then automatic captions, each in the order of `transcript.languages`.
   Human-made tracks match by region too (`en` matches `en-GB`); automatic captions match exactly, so `en-orig` (the
   original speech recognition) is not confused with machine translations.
3. A second yt-dlp call downloads that track as WebVTT, and `parse_vtt` turns it into segments, removing the repeated
   lines of rolling automatic captions. Reusing the saved extraction with `--load-info-json` does not work, because
   it needs the format list, which `video.json` leaves out.

Videos without a matching track fail with a `TranscriptError` that lists the available tracks. The audio provider
([asr_options.md](asr_options.md)) is the planned fallback for them. Details in
[implementation_notes.md](implementation_notes.md).

## Video listing

`list_latest_videos` fetches the channel's upload playlists without cookies, in parallel, and merges them newest
first:

- `UULF` (long-form videos) and `UULV` (livestream replays), plus `UUMF` and `UUMV` (members-only videos and replays)
  when `members = true`.
- `UU` (all uploads) is not used because it includes Shorts, which cannot be told apart in flat listings.
- `--extractor-args youtubetab:approximate_date` gives each entry an approximate timestamp. The merge uses it only to
  interleave playlists and keeps each playlist's own order, because old entries share coarse timestamps.
- A playlist the channel does not have counts as empty. Entries that are live now or upcoming are dropped.
- Unlisted perk videos linked only from the `/membership` tab are not listed.

Details and recheck commands are in [implementation_notes.md](implementation_notes.md).

## Browse step

`ces-youtube-tools browse [CHANNEL] [-n N] [-o DIR] [--refresh]` is the everyday command:

1. **Channel**: the `CHANNEL` argument (name or ID), else the only configured channel, else a numbered table of the
   configured channels.
2. **Video**: a numbered table of the channel's latest videos, the same listing as `list` (no cookies).
3. **Guide**: the same steps as `guide` (`_run_guide`). If the guide file exists, `browse` asks whether to overwrite
   it instead of failing, and keeps it on "no" without generating.

Menus are typed for now (`menu.parse_choice`): a number picks an item, an empty answer takes the default where there
is one, `q` quits with status 0, and anything else repeats the question with a hint. The Firefox profile picker in
`config init` / `config profile` uses the same helper.

## HTML pages and `serve`

A guide can be turned into a standalone HTML page that embeds the video's player, so that clicking a timestamp moves
the player instead of opening YouTube. The steps taken are in [html_guides_plan.md](html_guides_plan.md). What was
built:

- `ces-youtube-tools html PATH... [--overwrite]` (`guide_html.py`). A path is a Markdown file or a folder (its `.md`
  files, not its subfolders). Each page is written next to its guide, as `<same name>.html`; there is no output
  directory option, so two guides can never write the same page. An existing page is kept with a warning unless
  `--overwrite` is given. Files that are not `.md` are ignored, but a run that leaves no `.md` file is an error. A
  guide that cannot be converted (for example, no video link) is reported and makes the exit status 1.
- Conversion uses `markdown-it-py` (a direct dependency, although `rich` already pulls it in, because the code imports
  it) with raw HTML disabled, because a guide is model output and a `<script>` in it must be shown as text. The
  `commonmark` preset would pass raw HTML through, so `"html": False` is required. Tables are enabled (the prompt asks
  for them when a video compares items), `javascript:` and similar link targets are rejected by markdown-it itself,
  and `typographer` with smart quotes turns `"quotes"` and `--` into curly quotes and an en dash. Pandoc (an external
  binary) and rendering in the browser (needs JavaScript and a network connection to show anything) were rejected.
- The video is found from the guide's own links (the most common `v=` value; a link that cannot be parsed is
  skipped). `render_guide_page` rejects any ID that is not 11 characters of `A-Za-z0-9_-`, whichever caller passed it,
  because the template puts the ID in a URL and a JavaScript string without escaping. This check is what keeps the
  template safe. `html` reports a guide with no video link and skips it; `--html` passes the video's ID itself.
- `guide --html` and `browse --html` write the page next to the new guide, after the guide is written, from the
  corrected Markdown. The page is replaced whenever the guide is written (a new guide, `guide --force`, or an
  overwrite confirmed in `browse`), so it never lags behind the guide. When `browse` keeps an existing guide, the
  page is written only if missing; an existing page is kept with a warning (`html --overwrite` regenerates it). If
  the page fails, the Markdown guide stays and the exit status is 1.
- The page template is `templates/guide_page.html`, filled with `string.Template` (`$title`, `$video_id`, `$body`; a
  literal `$` must be written `$$`). CSS and JavaScript are inline, so one file is the whole page. It is not
  user-editable yet. The title comes from the guide's first level-one heading (without "Watching Guide: "), else the
  file name.
- The page depends only on timestamp links, not on the guide's structure, because a model's output, an edited prompt
  template or a hand-written guide may not follow the prompt. Style hints (`li.stamped`, `p.range`, `p.labeled`) are
  classes a short script adds by looking at how an element starts; without them the plain element styles apply.
- Player: a plain `<iframe>` in the HTML, which the IFrame API attaches to, so the video shows even if the API script
  fails. One click handler catches links to the same video with a `t=` value and calls `seekTo` and `playVideo`. An
  optional toggle (kept in `localStorage`, on by default) scrolls the player into view. Middle clicks and clicks with
  Ctrl, Cmd, Shift or Alt, links to other videos, and links clicked while the player is not ready, is hidden or has
  reported an error keep their normal behavior (open YouTube).
- The player is hidden, with a note that timestamps open YouTube, when the page is opened from disk (the note gives
  the `serve` command) and when the player reports an error (153: use `serve`; 100, 101, 150: private, removed or not
  embeddable; others: shown as a number). Without JavaScript, the page is a readable guide whose links open YouTube.
- Layout, the same on every screen size: the player at the top (16:9, centered, limited width) scrolls away with the
  page, so the guide gets the whole window; below it, the scroll toggle (hidden without JavaScript; `localStorage` is
  read and written inside `try`/`catch`, because some browsers block it for `file://` pages); then the guide, at most
  about 70 characters per line. Timestamp links are pills with fixed-width digits, picked out by a CSS selector on
  `t=` in the link. A clicked link flashes so the reader can find the place again, and scrolling is smooth unless
  `prefers-reduced-motion` is set. Tables scroll sideways when too wide. A print stylesheet hides the player and the
  toggle.
- The only external resources are the YouTube player and its API script (both need a network connection anyway) and
  the fonts.
- On a `file://` page the player is hidden before the page is drawn and the `<iframe>` is removed so it stops
  loading; the note shows the folder from the page's URL (inserted as text, not HTML). If only the IFrame API script
  fails to load, the player stays visible (it may still play) and the note says timestamps open YouTube.
- For a members-only video (yt-dlp `availability` is `subscriber_only`), `--html` adds a static hint under the player
  (`$members_hint` in the template): if the player asks the viewer to join, the browser is not sending the YouTube
  sign-in to the iframe, and in Firefox the fix is to turn off Enhanced Tracking Protection for the page. The hint is
  hidden together with the player. `html PATH` does not know the availability, so its pages have no hint.
- Light theme only. Fonts (Literata, Inter) come from Google Fonts with `display=swap`, so system fonts show at once
  when offline.
- The code is in `guide_html.py`, not `html.py`, which would shadow the standard library's `html` in some imports and
  in reading.
- `ces-youtube-tools serve [DIR] [--port 8765] [--no-open]` (`serve.py`) exists because YouTube refuses to play an
  embedded video on a page opened from disk (Error 153; see "The embedded player refuses `file://` pages" in
  [implementation_notes.md](implementation_notes.md), checked 2026-10-09). The player works from `localhost`.
  - It binds to `127.0.0.1` only, so the guides are not reachable from other machines.
  - It answers 403 when the `Host` header is not `127.0.0.1:<port>` or `localhost:<port>`. Without this, a web page
    could point its own domain at `127.0.0.1` (DNS rebinding) and read the folder through the browser.
  - `DIR` defaults to `guide.output_dir`, never to the current directory, which may hold browser cookie copies or
    `claude` session logs.
  - It uses `ThreadingHTTPServer` with `SimpleHTTPRequestHandler`, so the folder listing is the index page for now.
    Request logs go to the module logger at debug level, not stderr.
  - A port already in use is an error that suggests `--port`. Ctrl+C stops the server with status 0.
  - The default port is fixed, because `localStorage` is kept per origin and the origin includes the port.

## CLI conventions

- Register commands with `@command(APP, "name")` / `@command(CONFIG_APP, "name")` from `cli.py`, not
  `@APP.command(...)`. It passes Typer only the docstring's description; Typer would otherwise print the Google-style
  `Args:` / `Returns:` / `Raises:` sections in `--help`.
- Data a command prints for piping (`transcript`) goes to stdout. Status, warnings and errors go to stderr.
- The project is called `ces-youtube-tools` (distribution, command, config and cache directories) to avoid clashing
  with other tools. The Python package is `ces_youtube_tools`, the same name with underscores.

## Build order

1. Config: models, load/save, Firefox profile discovery and picker, `config` commands. (Done.)
2. Video listing: `ces-youtube-tools list CHANNEL`. (Done.) See "Video listing" below.
3. Transcript: yt-dlp subtitle provider, VTT parser with duplicate removal, work-directory cache,
   `ces-youtube-tools transcript VIDEO`. (Done.) See "Transcript step" above.
4. Guide: prompt template and builder, `ClaudeCliGenerator`, `guide VIDEO` command. (Done.) See "Guide step"
   above.
5. `browse`: interactive channel and video menus on top of steps 1–4. (Done, with a typed menu.) See "Browse step"
   above. Revisit the prompt library together with the interactive config session below.
6. HTML pages: `html`, `serve`, and the `--html` flag on `guide` and `browse`. (Done.) See "HTML pages and `serve`"
   above. Members-only videos play in the embedded player only if the browser lets the iframe send YouTube's cookies
   (verified in Firefox, see the implementation notes); the page cannot detect the case. Still to check by hand:
   Chrome cookie settings and embedding turned off (see the plan). `youtube-nocookie.com` was ruled out as a default
   (see "Out of scope" in the plan).

## Planned: interactive config session

Not needed for the first version. The `config` subcommands stay as they are, for scripting.

`ces-youtube-tools config` with no subcommand starts a session:

```
$ ces-youtube-tools config
No config found. Let's create one.
<Firefox profile picker, as in `config init`>

Channels: (none)
 [a] add channel  [r] remove  [m] toggle members-only  [p] change profile
 [s] save and quit  [q] quit without saving
> a
Channel handle or URL: @TheYoungTurks
Found The Young Turks (UC1yBKRuGpC1tSM73A0ZjYjQ). Add? [Y/n]
Show members-only videos? [y/N]: y
> s
Saved ~/.config/ces-youtube-tools/config.toml
```

- When the config is missing, the session runs the first-run steps (profile, then channels). Otherwise it opens the
  menu with a summary of the current config.
- Edits stay in memory and are written once on **save**. **Quit** leaves the file untouched.

What it takes:

1. **Move the rules out of the commands.** Duplicate checks and add/remove/toggle logic become plain functions on
   `AppConfig` (`add_channel`, `remove_channel`, `set_members`). The subcommands and the menu both call them, and they
   are tested without terminal input. This refactor is worth doing on its own.
2. **A menu loop** over those functions. Reuse `_pick_profile()`, and show a Rich spinner during the channel lookup
   (yt-dlp takes a few seconds).
3. **Typer:** replace `no_args_is_help=True` on `CONFIG_APP` with a callback using `invoke_without_command=True`.

UI options:

| Approach | New dependency | UX | Testing |
| --- | --- | --- | --- |
| Typed menu (`typer.prompt` + Rich) | None | Letters and numbers | `CliRunner` with scripted input |
| Arrow-key prompts | `questionary` (`prompt_toolkit`) | Arrow keys, checkboxes | Prompts need a real terminal, so test the functions from step 1 and keep the menu thin |
| Full TUI | `textual` | Full-screen forms | `App.run_test()`. Too much for a dozen settings |

Pick `questionary` if `browse` uses arrow-key lists, so both menus share one library. Otherwise the typed menu is
enough.
