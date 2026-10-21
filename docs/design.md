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

 ces-youtube-tools site [DIR]          (static site)
   Markdown files ─► guide_html ─► "<video id>.html" (embedded player) + index.html ─► OUT
   deploy.sh: check Access ─► site -o _site ─► npx wrangler pages deploy ─► Cloudflare Pages, behind Access
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

[site]
output_dir = "~/guides-site"   # where `site` writes the site, cleared on each build; unset = pass `-o`
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
5. **Output**: `Watching Guide - <YYYYMMDD> - <title>.md`, named by the upload date (left out when unknown or
   malformed), in `--output-dir`, else `guide.output_dir`, else the current directory. The date prefix makes guides
   sort by upload date. An existing file is refused before generating (no wasted generation) unless `--force`.
   The file starts with front matter written from `VideoInfo` (see "Guides stay Markdown, with front matter"
   below); the raw reply in the work directory has none.

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
cache. An unreadable cache file is logged and rebuilt. A work directory or cache file that cannot be written stops the
command with an error naming the path, before any generation (`CacheError`, or `TranscriptError` for the `subtitles/`
folder). The one exception is the raw reply (`guide.md`), saved after the generation: if it cannot be written, the
guide is still written and a warning is shown.

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

## Guide pages

A guide is turned into a standalone HTML page that embeds the video's player, so that clicking a timestamp moves the
player instead of opening YouTube (`guide_html.py`). The `site` command writes these pages; see "`site` command"
below. The first steps are in [html_guides_plan.md](html_guides_plan.md), which describes the `html` and `serve`
commands that `site` has since replaced. What was built:

- Conversion uses `markdown-it-py` (a direct dependency, although `rich` already pulls it in, because the code imports
  it) with raw HTML disabled, because a guide is model output and a `<script>` in it must be shown as text. The
  `commonmark` preset would pass raw HTML through, so `"html": False` is required. Tables are enabled (the prompt asks
  for them when a video compares items), `javascript:` and similar link targets are rejected by markdown-it itself,
  and `typographer` with smart quotes turns `"quotes"` and `--` into curly quotes and an en dash. Pandoc (an external
  binary) and rendering in the browser (needs JavaScript and a network connection to show anything) were rejected.
- The video is found from the front matter's `video_id`, else from the guide's own links (the most common `v=`
  value; a link that cannot be parsed is skipped). `render_guide_page` rejects any ID that is not 11 characters of
  `A-Za-z0-9_-`, whichever caller passed it, because the template puts the ID in a URL and a JavaScript string
  without escaping. This check is what keeps the template safe. `site` reports a guide with no video ID as a problem.
- The page template is `templates/guide_page.html`, filled with `string.Template` (`$title`, `$video_id`, `$body`,
  `$members_hint`, `$index_link`; a literal `$` must be written `$$`). CSS and JavaScript are inline, so one file is the
  whole page. It is not user-editable yet. The title comes from the guide's first level-one heading (without
  "Watching Guide: "), else the file name.
- The page depends only on timestamp links, not on the guide's structure, because a model's output, an edited prompt
  template or a hand-written guide may not follow the prompt. Style hints (`li.stamped`, `p.range`, `p.labeled`) are
  classes a short script adds by looking at how an element starts; without them the plain element styles apply.
- Player: a plain `<iframe>` in the HTML, which the IFrame API attaches to, so the video shows even if the API script
  fails. One click handler catches links to the same video with a `t=` value and calls `seekTo` and `playVideo`. An
  optional toggle (kept in `localStorage`, on by default) scrolls the player into view. Middle clicks and clicks with
  Ctrl, Cmd, Shift or Alt, links to other videos, and links clicked while the player is not ready, is hidden or has
  reported an error keep their normal behavior (open YouTube).
- The player is hidden, with a note that timestamps open YouTube, when the player reports an error (153: the page sent
  no referrer, which is also what a page opened from disk gets; 100, 101, 150: private, removed or not embeddable;
  others: shown as a number). Notes are plain text (`textContent`). Without JavaScript, the page is a readable guide
  whose links open YouTube.
- Layout, the same on every screen size: the player at the top (16:9, centered, limited width) scrolls away with the
  page, so the guide gets the whole window; below it, the scroll toggle (hidden without JavaScript; `localStorage` is
  read and written inside `try`/`catch`, because a browser may block site data); then the guide, at most about 70
  characters per line. Timestamp links are pills with fixed-width digits, picked out by a CSS selector on `t=` in the
  link. A clicked link flashes so the reader can find the place again, and scrolling is smooth unless
  `prefers-reduced-motion` is set. Tables scroll sideways when too wide. A print stylesheet hides the player and the
  toggle.
- The only external resources are the YouTube player and its API script (both need a network connection anyway) and
  the fonts.
- If only the IFrame API script fails to load, the player stays visible (it may still play) and the note says timestamps
  open YouTube. Pages opened from disk (`file://`) are not supported (see "`site` command"); the check that hid the
  player on them before it was drawn was removed on 2026-10-21.
- For a members-only video (`members_only: true` in the front matter, from yt-dlp's `availability` of
  `subscriber_only`), the page gets a static hint under the player (`$members_hint` in the template): if the player
  asks the viewer to join, the browser is not sending the YouTube sign-in to the iframe, and in Firefox the fix is to
  turn off Enhanced Tracking Protection for the page. The hint is hidden together with the player. A guide without
  front matter gets no hint.
- Light theme only. Fonts (Literata, Inter) come from Google Fonts with `display=swap`, so system fonts show at once
  when offline.
- The code is in `guide_html.py`, not `html.py`, which would shadow the standard library's `html` in some imports and
  in reading.
- Removed on 2026-10-20, replaced by `site`: `html PATH... [--overwrite]`, which wrote each page next to its guide;
  `serve`, a server on `127.0.0.1`, because the player refuses pages opened from disk (Error 153; see "The embedded
  player refuses `file://` pages" in [implementation_notes.md](implementation_notes.md)); and `--html` on `guide` and
  `browse`. Pages on the site come from Cloudflare Pages over HTTPS, where the player works, and can be read on any
  device. `html` pages already written next to guides are left alone; `site` reads only the `.md` files.
  `expand_paths` (files and folders to guides) stays in `guide_html.py` for `backfill` and `site`.

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
6. HTML pages: `html`, `serve`, and the `--html` flag on `guide` and `browse`. (Done; removed in step 7, replaced by
   `site`.) See "Guide pages" above. Members-only videos play in the embedded player only if the browser lets the iframe
   send YouTube's cookies (verified in Firefox, see the implementation notes); the page cannot detect the case. Still to
   check by hand: Chrome cookie settings and embedding turned off (see the plan). `youtube-nocookie.com` was ruled out
   as a default (see "Out of scope" in the plan).
7. Guide metadata and a published site: front matter, `backfill`, the `site` command, Cloudflare Access with the
   first deploy, `deploy.sh`, and removing `html`, `serve` and `--html`, which `site` replaces. (Done, 2026-10-20.)
   See "Guide metadata and a private site" below.
8. Serve the site over HTTP only: remove the guide page's `file://` handling and make the site's links
   extensionless. (Done, 2026-10-21.) See "`site` command" below.
9. An interactive index: sorting, grouping and filtering in the browser, from a JSON payload, with `node --test`
   tests, after recording publish and generation times in the guides. (Planned.) See "Planned: publish and generation
   times" and "Planned: an interactive index" below.

## Guide metadata and a private site

Publish the guide pages as a static site on Cloudflare Pages, with an index page, so guides can be read on any
device. A real HTTPS origin also satisfies the embedded player, which refuses `file://` pages; before the site, that
took a local server (`serve`, now removed).

### Guides stay Markdown, with front matter

The model keeps writing Markdown. Switching its output to JSON was rejected:

- The page depends only on timestamp links, not on the guide's structure (see "Guide pages"). A Markdown
  reply that drifts from the prompt still renders; a JSON reply that drifts fails to parse and the generation is lost.
- Exact quotes in double quotes would all need escaping inside JSON strings, and tables would become nested arrays.
- The `.md` file must stay readable and hand-editable, and hand-written guides must keep working with `site`.
- `GuideGenerator` returns a string. A JSON contract would require structured output from every future generator.
- What the index page needs is metadata, which comes from `VideoInfo`, not from the model. Structure the site may want
  later (a table of contents, a one-paragraph blurb) can be read from markdown-it tokens, as `guide_title` does.

The tool, not the model, writes front matter at the top of each guide (`front_matter.py`, done). `write_guide`
fills it from `VideoInfo`:

```yaml
---
video_id: abcdefghijk
title: 'A video: part 1'          # the YouTube title, not the guide's heading
channel: A channel
channel_id: UCxxxxxxxxxxxxxxxxxxxxxx
upload_date: 2026-10-08           # null when unknown or malformed
duration: 600                     # seconds
members_only: false               # true for availability subscriber_only; null when yt-dlp did not report it
generated: 2026-10-09
---
```

- Format: YAML between `---` lines, which most Markdown tools read (Obsidian, Eleventy, Pandoc). It is written and
  read with PyYAML (`safe_dump`, `safe_load`) and validated with a Pydantic model (`GuideMetadata`). PyYAML quotes
  strings that would read back as another type (a title such as `yes` or `2026`), which a hand-rolled writer would
  get wrong. Every key is always written, unknown values as `null`, so all guides have the same shape. Lines are
  never folded, so a long title stays on one line.
- Reading: front matter starts at a `---` first line and ends at the next `---` line; without both, the whole file
  is Markdown. Every field is optional and unknown keys (for example, tags added in a notes app) are ignored, because
  a guide may be hand-edited. Invalid YAML, a block that is not `key: value` lines, or a value of the wrong type is
  an error that names the file (and, for invalid YAML, the line). A hand-written guide whose first line is a `---`
  rule is read as front matter, as in Jekyll, Eleventy and Pandoc; start it with a blank line instead.
- `site` removes the front matter before rendering (otherwise markdown-it would show it as a rule and a heading) and
  takes the video ID and the members-only hint from it. Guides without front matter fall back to the links for the
  video ID. A `video_id` from the front matter is still checked by
  `render_guide_page`, like any other.
- `members_only` has three states. The `site` command treats `null` (unknown) as members-only.
- The generator and model are not recorded yet: `GuideGenerator` does not report them. Add them if comparing guides
  across models becomes useful.

`ces-youtube-tools backfill PATH... [--dry-run]` (`backfill.py`, done) adds front matter to guides written before it
existed. A path is a Markdown file or a folder (its `.md` files, not its subfolders).

- The video ID comes from the guide's links (`find_video_id`). The metadata comes from the cached `video.json`, else
  it is fetched with `load_video_info` (yt-dlp with cookies, because members-only videos need them) and cached.
  Fetching is the default, so every backfilled guide gets complete metadata.
- Two passes. The first reads every guide and loads every video's metadata, and reports all failures together (no
  video link, unreadable file, yt-dlp or cache error). If any guide fails, no guide is changed and the exit status
  is 1. The second pass writes the guides. A write failure stops it with a message saying how many were written;
  running again skips them.
- A guide with a front matter block, valid or not (`has_front_matter`), is skipped, so the command can be run again
  safely and never stacks a second block on a hand-edited one.
- The front matter goes above the guide's bytes, unchanged (line endings included). The new file is written next to
  the guide (`.<name>.tmp`, with the guide's permissions) and moved over it, so a failure never leaves half a guide.
  For a symlinked guide, the file it points to is written and the link is kept.
- `generated` is `null`: nothing recorded when the guide was written, and file times change when files are copied.
- `--dry-run` runs the first pass, including fetching (which fills the cache), and lists the guides it would write.

### Planned: publish and generation times

Decided on 2026-10-21, for the interactive index (below), which sorts by these. The steps are Step 1 in
[20261021_interactive_index_implementation.md](20261021_interactive_index_implementation.md).

- `upload_date` is a UTC day (or, with no exact time, a US/Pacific day or the page's date text, per yt-dlp's source, not
  yet verified), so US afternoon uploads read a day early in Asia/Taipei. yt-dlp also reports `timestamp` and, for
  streams, `release_timestamp` (when the stream started; `timestamp` was about when it ended), which the tool drops
  today. See "Upload dates are UTC days" in [implementation_notes.md](implementation_notes.md).
- The publish time is `release_timestamp`, else `timestamp`; `upload_date` is the fallback when both are missing.
- Front matter records times in UTC, as strings such as `2026-10-14T16:03:12Z`: a new `published` key, and
  `generated` becomes the time the guide was written. `upload_date` stays as yt-dlp reports it.
- File names take the publish time's day in `guide.file_name_timezone` (an IANA name; default: the system's time
  zone), so a guide is named by the day it was published where the owner lives. The prompt's `$upload_date` is the
  same day.
- The prompt no longer asks for the `$today · $channel` line under the heading: the front matter holds both dates,
  and a copy in the text can disagree with it.
- Because names can change, `guide` refuses a video that already has a guide in the output folder under any name
  (unless `--force`), and `backfill` rewrites the front matter of every guide and renames it to the new rule. It keeps
  a `generated` that is set, fills a `null` one with the publish time (an approximation, chosen over reading the
  guides' date line for simplicity), and keeps keys it does not know.
- `backfill` is no longer all-or-nothing: a guide with a problem of its own (no video ID, invalid front matter,
  metadata that cannot be loaded) is left as it is and listed, and the others are written; problems between guides
  (two guides for one video, a new name already taken) still stop the run before anything is written. Since every
  guide is rewritten on every run, one deleted or private video would otherwise block every run.
- The index turns the UTC times into days in the browser's time zone.

### `site` command

`ces-youtube-tools site [DIR] [-o OUT] [--include-members-only]` (`static_site.py`, done) writes the whole site into
`OUT`: one page per guide, `index.html`, `robots.txt` and a `_headers` file. `DIR` defaults to `guide.output_dir`, and
its `.md` files (not its subfolders) are the guides. `OUT` defaults to `site.output_dir`; with neither, the command
fails rather than picking a folder to clear. Deploying is kept outside the tool; the command prints
`npx wrangler pages deploy OUT`.

- `deploy.sh` in the repository root is how the site is deployed. It checks that Access still guards the site (see
  below), builds with `site -o _site --include-members-only`, and runs `npx wrangler pages deploy --branch=main`, so
  it always deploys to production, the host name it checked, whatever git branch is checked out. `wrangler.toml`
  names the Pages project and sets `pages_build_output_dir = "_site"`, so the script passes `-o _site` rather than
  relying on `site.output_dir`. `_site` and wrangler's `.wrangler` cache folder are gitignored. Wrangler is not
  pinned: `npx -y` runs the latest version.
- `--include-members-only` has no config setting on purpose: a build for any other destination leaves members-only
  guides out unless asked. `deploy.sh` passes it because it deploys only to this project, which is behind Access,
  and refuses to deploy if Access is not in front of it.

- Built in Python with the existing page template, not with a static site generator such as Eleventy. Eleventy would
  add a Node toolchain to repeat rendering this project already does, and its Markdown setup passes raw HTML through
  by default, undoing `"html": False`, which keeps a `<script>` in model output from running.
- The module is `static_site.py`, not `site.py`, which would shadow the standard library's `site` module.
- A page is named after its video, `<video_id>.html`: the ID is checked to be URL-safe, and the URL stays the same
  when the guide's file is renamed. Two guides for the same video are an error. Links leave out `.html`
  (`<video_id>`, and `./` for the index), so a click does not go through Cloudflare Pages' redirect (308, not yet
  verified) from the `.html` name. Pages serves `/<video_id>` from `<video_id>.html` (checked on 2026-10-21, see the
  implementation notes), and `./` from `index.html` (checked on 2026-10-21).
- The site is served only over HTTP: by Cloudflare Pages, or locally with `npx wrangler pages dev _site`, which is
  expected to serve the build the way Pages does, extensionless names and `_headers` included (not yet verified).
  Opening the built files from disk (`file://`) is not supported, decided on 2026-10-21: nobody reads the site that
  way, the embedded player refuses such pages anyway (Error 153), and supporting it constrained the link format and
  the index script. The Markdown guides stay readable without the site. The guide page's `file://` handling (the
  early check in `<head>`, the branch that removed the player, and its note) was removed on 2026-10-21; opened from
  disk, the player's Error 153 is still caught by the error note. Links were made extensionless on 2026-10-21.
- Each page gets an "All guides" link to `./` above the player (`render_guide_page(link_to_index=True)`).
- The index (`templates/site_index.html`) lists guides newest upload first (no date last, then by title), with the
  upload date, channel, length, and a badge for members-only or unknown availability. The title is the YouTube title
  from the front matter, else the guide's heading, else the file name.
- Two passes, as in `backfill`. The first reads and renders every guide in memory and reports all problems together
  (unreadable file, invalid front matter, no video ID, two guides for one video); if there is any, `OUT` is not
  touched and the exit status is 1. The front matter of guides that are left out is checked too.
- `OUT` is rebuilt from scratch on every run, because a deployment uploads every file in the folder and a stale page
  would stay published. To avoid deleting the wrong folder, `site` only clears a folder that is empty or holds its
  marker file (`.ces-youtube-tools-site`, which is deployed too; it says the folder is cleared on each build). `OUT`
  may not hold any guide that was read, published or left out, directly or in a subfolder, marker or not; for a
  symlinked guide, neither the link's folder nor the target's. The check uses the guide files themselves, not the
  `DIR` argument, so it cannot disagree with how the guides were found (`~` in `DIR` once slipped past a check on
  `DIR`). `~` in `OUT` is expanded too, for `--out=~/x`, which the shell does not expand. Symbolic links in `OUT` are
  removed, not followed. If writing fails after `OUT` was cleared, the error says the folder is incomplete.
- `_headers` sets `X-Robots-Tag: noindex, nofollow` and `Referrer-Policy: strict-origin-when-cross-origin` for every
  path. The referrer policy is the browsers' default, stated so it cannot be tightened by accident: with
  `no-referrer` the embedded player fails with Error 153, as on `file://` pages (see the implementation notes).

### Access: the site is private

Hiding the site is not enough; it must be private. `robots.txt` only asks crawlers to stay away. Links leak through
browser history sync, shared links and referrers, and `*.pages.dev` host names are public. An index page puts every
guide one click from the front page.

- The whole Pages project is behind Cloudflare Access, with a policy that allows only the owner's email. This covers
  every host name that serves the project: `<project>.pages.dev`, the preview deployments (`*.<project>.pages.dev`)
  and any custom domain. An uncovered host name serves the whole site to anyone.
- `X-Robots-Tag: noindex` (in `_headers`) and a `robots.txt` that disallows everything are extra layers, not the
  protection.
- Members-only guides are left out of a build unless `--include-members-only` is given. A guide without front matter
  (availability unknown) counts as members-only. This keeps them unpublished if the Access policy is ever missing or
  misconfigured. `deploy.sh` includes them, so instead it first requests `https://<project>.pages.dev/` without a
  session and stops unless the answer redirects to `<team>.cloudflareaccess.com`. This checks the production host
  name only; the wildcard entry for deployment host names is checked by hand, with the commands below.
- Why members-only guides need this: a guide carries the substance of the video (every segment summarized, exact
  quotes, numbers, tables), which is what members pay for. YouTube's player blocks the video for non-members, but
  that check does not cover the guide's text. Publishing it would distribute a derivative of the creator's
  members-only content, and the video IDs tie it to the owner's membership.
- Check after setting up or changing Access, without a browser session: every host name must answer with a redirect
  to `<team>.cloudflareaccess.com`, not with the page.

  ```
  curl -sI https://<project>.pages.dev/ | grep -i -E '^(HTTP|location)'
  curl -sI https://<hash>.<project>.pages.dev/ | grep -i -E '^(HTTP|location)'
  ```

  `npx wrangler pages deployment list --project-name <project>` lists the `<hash>` host names.
- Set up on 2026-10-20 (done): the Pages project `ces-youtube-watching-guides`, behind an Access application that
  covers `ces-youtube-watching-guides.pages.dev` and `*.ces-youtube-watching-guides.pages.dev`. Both host names were
  checked with the commands above before anything else was published; see "Cloudflare Pages and Access" in
  [implementation_notes.md](implementation_notes.md). A custom domain, if one is added, must be added to the Access
  application and checked the same way.

### Planned: an interactive index

The index is to be sorted, grouped and filtered in the browser. `site` writes the list of published guides into the
page as JSON, and a script renders it.

- Why in the browser: the site is static, so the browser is the only place a reader can change the view, and a change
  needs no rebuild. The data is small (a few hundred bytes per guide). Only the owner reads the site, through Access,
  in a modern browser, so the index may require JavaScript; a `<noscript>` line says so. Guide pages keep working
  without it.
- The payload is inline, in `<script type="application/json" id="guides">`, read with `JSON.parse`. Not a separate
  `guides.json`: inline means no second request, and no loading or error state to handle. The browser never runs a
  JSON script block, so it holds data only.
- Escaping: `json.dumps` leaves `<` as is, so a title containing `</script>` would end the block early. `site` writes
  `<`, `>` and `&` as `\u003c`, `\u003e` and `\u0026`, which `JSON.parse` turns back into the same characters. This
  is the payload's only escaping step, and it gets a test with such a title.
- Contents: built from the published pages only (`SiteBuild.pages`, never `left_out`), with only what the index shows:
  `video_id` (the link is `<video_id>`, see "`site` command"), `title`, `channel`, `published` and `generated` (UTC
  times, see "Planned: publish and generation times"; an ISO date for an old date-only `generated`), `upload_date`
  (ISO date, used only without `published`), `duration` (seconds) and `members_only` (`true` or `false`), each
  `null` when unknown. No description and no guide text. A test checks that a left-out guide's ID is not in the page.
- Rendering uses `createElement` and `textContent`, never `innerHTML`: titles and channel names come from YouTube and
  are untrusted. This replaces the `html.escape` calls in `_index_item`.
- The view (sort key and order, grouping, filter text) is kept in the URL hash, such as `#sort=channel&group=month`,
  so reloading, the back button and saved links keep it. `localStorage` remembers the last sort, order and grouping
  (decided on 2026-10-21), so the guide pages' "All guides" link, which carries no hash, opens the index as it was
  left. A view in the hash wins over the stored one. The filter text is not stored, so a later visit never starts
  with guides hidden. Read and written inside `try`/`catch`, as on the guide page. Details in
  [20261021_interactive_index_implementation.md](20261021_interactive_index_implementation.md), Step D.
- The default view sorts by publish date, newest first, then by channel, then by title; the second date sort is the
  generation date, also then by channel and title (decided on 2026-10-21). Each date is a day in the browser's time
  zone. Guides without the date come last.
- No pagination at first. Pagination breaks Ctrl+F and scanning the list; grouping (by channel or month) and the
  filter help more. If the list grows too long, render it in steps ("show more") before numbered pages. With the data
  in memory, either is a slice of the array.
- No template engine. Jinja2 was considered for loops and automatic escaping in the index, but with the rendering in
  the browser the template keeps only a few placeholders (`$guides_json`, `$count`), and `string.Template` is enough.
  `$count` fills only the text shown before the script runs; the script then sets the count and the sort label from
  the current view, since a filter or another sort makes the built text wrong. Reconsider a template engine if HTML
  built at build time grows elsewhere.
- Code layout: the script is `templates/site_index.mjs`, an ES module written to `OUT` as `index.mjs` and loaded with
  `<script type="module" src="index.mjs">`. The `.mjs` name makes Node load it as a module without a `package.json`
  or relying on its syntax detection; Cloudflare Pages is expected to serve it as JavaScript, which module scripts
  require (not yet verified). It is not inlined, so the tests import the same file. The pure functions
  (sorting, grouping, filtering, reading and writing the hash) are exported with `export function` and kept apart
  from the code that touches the page, which runs only when there is a `document`, so importing the file in Node has
  no side effects. Module scripts do not load from `file://` pages, which the site no longer supports (see "`site`
  command").
- Tests: `node --test` files in `tests/js/` (`*.test.mjs`) import the pure functions and test them. A pytest test
  collects those files with `pathlib` and passes them to `node --test` by name (Node 21 and later read a folder
  argument as a module path, not a folder of tests), and fails with Node's output if they fail; it is skipped when
  `node` is not on `PATH`, so `uv run pytest` still works without Node. The Python side (payload, escaping, left-out
  guides, `index.mjs` written) is tested in pytest as usual.

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
