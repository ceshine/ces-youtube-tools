# Interactive index: implementation plan

Written on 2026-10-21. The design is in "Planned: an interactive index" in [design.md](design.md) (build order items 8
and 9); this file lists the steps to build it. Each step passes the tests and can be deployed on its own. Steps 0 and
1 change the links and the guides' metadata; Steps A to D build the index on top of them.

## Open decisions

- Order: Step 0 first, so the payload's link format is final from the start (recommended), or keep the `.html` links
  and do the index first.
- Scope of Step C: sort, group (channel, month) and a text filter, as below; or also a members-only filter or
  grouping by year.
- Sort keys besides the two dates in Step C: keep title, channel and length, or offer only the two dates.

Decided on 2026-10-21:

- Step D is required, not optional.
- Step 1: guides record a publish time and a generation time in UTC; the index sorts by the publish date (default)
  or the generation date, each a day in the browser's time zone, then by channel, then by title.
- Step 1b: `backfill` is no longer all-or-nothing. A guide with a problem of its own is left as it is and listed,
  and the others are written; only problems between guides stop the run. It rewrites every guide on every run, so
  one deleted or private video would otherwise block every run.

## Step 0: extensionless links (rest of build order item 8)

- `SitePage.filename` stays `<video_id>.html`, the file written. `_index_item` links to `page.video_id` instead of
  `page.filename`. No `href` property: Step B removes `_index_item`, and the script builds the link from the payload.
- The guide page's "All guides" link becomes `./` instead of `index.html` (`_INDEX_LINK` in `guide_html.py`), and the
  `render_guide_page` docstring ("Add a link to `index.html`") says so.
- Update the tests that check for `index.html` links and `.html` hrefs (`test_guide_html.py`, `test_static_site.py`).
- Cloudflare Pages serves `/<video_id>` from `<video_id>.html` (checked on 2026-10-21 on the production host name;
  see "Pages serves `/<video_id>`" in [implementation_notes.md](implementation_notes.md)), and `./` from `index.html`
  (checked after deploying Step 0, the same day). Not yet verified: the same for `npx wrangler pages dev`.

## Step 1: publish and generation times

The index sorts by these, so they come before Step A. Two commits: 1a for new guides, 1b for `backfill`.

Why: yt-dlp's `upload_date` is a UTC day (or, with no exact time, a US/Pacific day or the page's date text, per
yt-dlp's source, not yet verified), so US afternoon uploads read a day early in Asia/Taipei. yt-dlp also reports
exact times, which the tool drops today. See "Upload dates are UTC days" in
[implementation_notes.md](implementation_notes.md) (checked on 2026-10-21, with cookies).

### Step 1a: record the times for new guides

- `VideoInfo` gains `timestamp` and `release_timestamp` (Unix seconds or `None`), copied in `video_info_from_ytdlp`.
  The publish time is `release_timestamp`, else `timestamp`: for a stream replay, `release_timestamp` was when the
  stream started and `timestamp` about when it ended. `upload_date` is used only when both are missing.
- Cache: a `video.json` without the `timestamp` or `release_timestamp` key was written before this step, so it is
  stale and the metadata is fetched again. A key that is present with `null` means yt-dlp did not report it.
  `model_validate_json` cannot tell the two apart (both fields default to `None`), so `load_video_info` checks the
  raw JSON object for the keys before validating it. Making the fields required was rejected: every old file would
  log an invalid-cache warning, and every `VideoInfo(...)` in the tests would need the two arguments.
- Front matter: a new `published` key, and `generated` becomes a time. Both are UTC strings such as
  `2026-10-14T16:03:12Z`, written by the tool as strings (PyYAML's `safe_dump` writes a `datetime` as `2026-10-14
  16:03:12+00:00`) and checked with Pydantic's `AwareDatetime`. Tests cover reading the value quoted and unquoted.
  `generated` is when the guide is written, to the second: `write_guide` takes an aware `now: datetime.datetime`
  instead of `today: datetime.date` (`_run_guide` passes the current time; tests pass a fixed one). `$today` is the
  local date of `now`, and `generated` is `now` in UTC. `upload_date` stays, as yt-dlp reports it. A
  date-only `generated`, from a guide written before this step, is still read, as a date (all 16 guides so far have
  `null`).
- File name: `Watching Guide - <YYYYMMDD> - <title>.md`, where the date is the publish time's day in
  `guide.file_name_timezone`; without a publish time, `upload_date` as now. The setting is
  `file_name_timezone: str | None = None`, an IANA name such as `"Asia/Taipei"`, checked with `zoneinfo.ZoneInfo` in a
  validator so that an unknown name is a `ConfigError`. Unset, it means the system's local time zone, and `config
  options` shows it as "(unset)". With it unset, running `backfill` on a machine set to another time zone renames
  guides, so setting it is recommended.
- Prompt: `$upload_date` ("Oct 8, 2026") is the same day as the file name. The built-in template drops item 2 ("A line
  with the guide date and the channel: `$today · $channel`"), because the front matter holds both dates and a second
  copy in the text can disagree. `$today` stays available to edited templates. A copy written by `config template`
  keeps the line until it is written again with `config template --force`.
- Duplicates: a changed file-name rule could give a video's new guide a different name from its old one. Before
  generating, `guide` reads the `.md` files in the output folder and refuses when one is about the same video (its
  front matter `video_id`, else the video its links point to most, `find_video_id`, as in `site`), naming that file,
  unless `--force`. With `--force`, the new guide is written under its own name and the old file is removed if the
  names differ. `browse` has no `--force`: it asks whether to replace the guide, naming the old file, and a yes does
  what `--force` does. The check on the file name stays.
- A `.md` file in the output folder that cannot be read, or whose front matter is invalid, never stops a guide from
  being written: its links are used instead (or it is skipped if it cannot be read), with a warning naming it.
- Tests:
  - The publish time is `release_timestamp` when both are set; `upload_date` is used only when neither is.
  - A publish time of `16:30Z` names the file with the next day in `Asia/Taipei`.
  - A `video.json` missing the new keys is fetched again; one with `null` values is used.
  - `published` and `generated` round-trip through the front matter; a date-only `generated` still reads.
  - A guide for the same video under another name is refused; `--force` replaces it and removes the old file.

### Step 1b: `backfill` rewrites every guide

`backfill PATH... [--dry-run] [--refresh]` rewrites the front matter of every guide, not only of guides without it.
`--refresh` is a new flag.

- The video ID comes from the front matter's `video_id`, else from the guide's links (`find_video_id`), as in
  `site`.
- Keys that come from the video (`video_id`, `title`, `channel`, `channel_id`, `upload_date`, `published`,
  `duration`, `members_only`) are rewritten from its metadata.
- `generated` is kept when set. When it is `null`, it is set to the publish time (decided on 2026-10-21: the date in
  the guides' own date line was not used, to keep this simple; new guides record the real time). Without a publish
  time it stays `null`.
- Keys the tool does not know (such as tags added in a notes app) are kept, after the known keys. YAML comments in
  the front matter are lost.
- Metadata: the cached `video.json`, unless it is stale (Step 1a) or `--refresh` is given; otherwise fetched with
  yt-dlp and cookies, as now.
- Renames: each guide is renamed to the name `guide` would give it now. Page URLs use the video ID, so the site does
  not change.
- Problems:
  - With one guide (no video ID in the front matter and no video link, invalid front matter, metadata that cannot be
    loaded): that guide is left as it is and listed, the others are written, and the exit status is 1.
  - Between guides (two guides for one video, a new name already taken by another file): found in the first pass;
    nothing is written, and the exit status is 1.
- A guide whose front matter and name would not change is not written and is reported as unchanged, so running
  `backfill` again changes nothing.
- The text below the front matter is kept byte for byte, including the date line older guides have.
- Writing a regular file: the new file is written next to the guide and moved into place, as now; when the name
  changes, it is moved to the new name and the old file is then removed.
- Writing a symbolic link: the file it points to is written in place, as now, and the link keeps pointing to it.
  When the name changes, the link itself is renamed; the target keeps its name.
- `--dry-run` lists every rename and every key that would change.
- Tests: unknown keys kept; a set `generated` kept; a `null` one filled from the publish time; renames, including a
  taken name; a second run writes nothing; one failing video leaves the others written.

## Step A: write the payload next to the current list

The page looks the same; the data is added and tested.

- `static_site.py`: `index_payload(pages) -> list[dict]` with exactly `video_id`, `title`, `channel`, `published`
  (UTC string or `null`), `upload_date` (ISO date or `null`, used only without `published`), `generated` (UTC string,
  ISO date for an old date-only value, or `null`), `duration` (seconds or `null`) and `members_only` (`true`, `false`
  or `null`).
- `_script_json(data) -> str`: `json.dumps(..., ensure_ascii=False)`, then `<`, `>` and `&` written as
  `\u003c`, `\u003e` and `\u0026`.
- `templates/site_index.html`: `<script type="application/json" id="guides">$guides_json</script>`.
- Tests:
  - A title containing `</script>` stays inside the block, and `json.loads` of the block returns it unchanged.
  - A left-out guide's video ID is nowhere in `index.html`.
  - Each entry has exactly the eight keys: no description, no guide text.

## Step B: the script renders the current view, and the list built in Python is removed

- `templates/site_index.mjs`, with the pure functions exported (`export function`):
  - `formatDuration`, the same output as Python's `format_duration` (`H:MM:SS` or `M:SS`, empty when unknown).
  - `compareGuides`, `sortGuides`, `filterGuides` and `groupGuides`.
  - `parseView` and `formatView`, for the URL hash.
- The code that touches the page is a `render(view)` function, started by `if (typeof document !== "undefined")
  main();`, so importing the file in Node has no side effects.
- Rendering uses `createElement` and `textContent` only, and builds the same markup as `_index_item` does now
  (`<ol class="guides">`, `.meta`, `.badge`), so the CSS does not change.
- `write_site` also writes `index.mjs`, read with `importlib.resources`. The wheel already ships every file in the
  package (`packages = ["src/ces_youtube_tools"]` in `pyproject.toml`); a test reads it through `resources`.
- `templates/site_index.html`: `<script type="module" src="index.mjs">` and a `<noscript>` line saying the index needs
  JavaScript. `$listing` goes; `$count` stays, as the text shown before the script runs.
- Remove `_index_item` and the imports only it uses (`html.escape`, `format_duration`, or `ruff check` fails with
  F401); rewrite the `test_render_index_*` tests around the payload. `test_write_site_writes_every_file` lists every
  file written, so it gains `index.mjs`.
- `_newest_first` stays: it keeps the payload in a stable order, so a build with the same guides gives the same
  output. The page does not rely on that order; the script always sorts. A JS test checks that the default sort puts
  newer publish dates first and unknown dates last, with ties by channel, then title, as in Step C; this can differ
  from Python's order.
- Tests:
  - `tests/js/site_index.test.mjs`, with `node:test` and `node:assert/strict`.
  - `tests/test_js.py`: skipped when `shutil.which("node")` is `None`; collects `tests/js/*.test.mjs` with `pathlib`,
    runs `node --test` with the files by name, and fails with Node's output. It runs Node twice, with `TZ` set to
    `Asia/Taipei` and to `America/Los_Angeles`, so days are tested on both sides of UTC.

## Step C: the controls

- Sort, ascending or descending (descending by default for the dates):
  - Publish date (default): the day of `published` in the browser's time zone, else `upload_date` as it is; then
    channel, then title.
  - Generation date: the day of `generated` in the browser's time zone (a date-only value as it is); then channel,
    then title.
  - Title, channel or length, unless the open decision drops them; ties go by title.
  - In the hash and the stored view, the keys are `published`, `generated`, `title`, `channel` and `length`.
  - Channel and title stay A to Z when the dates are reversed. Unknown values (no date, no length) go last in both
    directions.
- The days are local on purpose: the stored times are UTC, and the page shows and sorts by the reader's day. A row
  shows its publish date, and also its generation date when the view sorts by it.
- Titles and channels are compared with `Intl.Collator(undefined, { numeric: true, sensitivity: "base" })`, so "Part
  10" comes after "Part 9" and the order follows the browser's language. This differs slightly from Python's
  `casefold()` order, which only orders the payload. The Node tests pass a fixed locale.
- Group: none (default), channel or month. Each group is `<section><h2>October 2026 · 3</h2><ol>…`. Month groups follow
  the date order, channel groups are alphabetical, and "No date" or "Unknown channel" comes last.
- The month is that of the date the view sorts by (the publish date for the other sort keys), in the browser's time
  zone. A date-only value (`upload_date`, an old `generated`) is never turned into a `Date`: `new Date("2026-10-01")`
  is midnight UTC, which is still September west of UTC. Its month is its first seven characters (`2026-10`), and the
  heading is formatted from that month with `timeZone: "UTC"`.
- Filter: a text box matching title and channel, ignoring case after `normalize("NFKC")`; every word typed must
  match. Two empty states: "No guides yet." and "No guides match …".
- View in the hash, such as `#sort=published&order=desc&group=month&q=rust`:
  - `parseView` takes each setting on its own: an unknown or missing value becomes that setting's default, and the
    other settings are kept, since the hash can be edited by hand.
  - Changes are written with `history.replaceState`, so typing in the filter does not fill the back-button history.
  - A `hashchange` listener renders the view again when the hash is edited.
- The script writes the count line from the current view, such as "12 of 40 guides, grouped by month, newest publish
  date first."

## Step D: remember the view in `localStorage`

The hash keeps the view on reload and when going back from a guide, but the guide page's "All guides" link goes to
`./` without a hash and resets it. The index stores the last view in `localStorage` and restores it when the URL has
no hash. Made a required step on 2026-10-21.

- What is stored: sort, order and grouping, under the key `ces-youtube-tools:index-view` (the guide page uses
  `ces-youtube-tools:scroll-to-player`). The value is the same string `formatView` writes for the hash, such as
  `sort=title&order=asc&group=channel`, so one format and one parser (`parseView`) cover both.
- The filter text is not stored. A filter restored on a later visit would quietly hide guides, including ones added
  since; it stays in the hash, so reloading and going back keep it.
- Which view is used on load, first match wins:
  1. The hash, when it holds any view setting. A saved link or the back button shows the view it names.
  2. The stored view, which is then written into the hash with `history.replaceState`, so the URL shows it.
  3. The default: newest publish date first, no grouping.
- Saving: every change of sort, order or grouping writes the stored view, whether it comes from the controls or from
  a `hashchange` (the hash edited by hand, back or forward), since it is the view on screen. When the view equals the
  default, the key is removed instead, so the default can change in a later build without being shadowed by an old
  stored value.
- A "Reset view" button returns to the default (filter cleared) and removes the key. Without it, getting back to the
  default takes up to three changes.
- Storage may be missing or throw (blocked site data, private windows). Every read and write is inside `try`/`catch`,
  as on the guide page; on failure the index works as without Step D. A stored value goes through `parseView` like a
  hash: an unknown value becomes that setting's default, and the other settings are kept.
- Code, as pure functions that the Node tests call with a stub storage object:
  - `initialView(hash, storage)` applies the order above and returns the view and whether the hash must be
    rewritten (true when the stored view was used).
  - `saveView(storage, view)` writes or removes the key.
  - `main()` passes `location.hash` and `window.localStorage`, read inside the `try`, since reading the property
    itself can throw; when it throws, `main()` passes `null`. With `null`, `initialView` gives the hash or the
    default, and `saveView` does nothing.
- Tests (`tests/js/site_index.test.mjs`), through `initialView` and `saveView`:
  - The hash beats the stored view; the stored view beats the default, and asks for the hash to be rewritten.
  - `saveView` leaves out the filter text, and removes the key for the default view.
  - A stub whose `getItem` and `setItem` throw gives the default and no error; so does `null` instead of a storage
    object.
  - A stored value with an unknown sort keeps its stored grouping; one that is not a view setting at all gives the
    default.

## Docs, as each step lands

- [design.md](design.md): mark build order items 8 and 9 done; turn "Planned: an interactive index" and "Planned:
  publish and generation times" into descriptions of what was built; update the `site` bullets about `.html` links,
  including "Each page gets an 'All guides' link to `index.html`"; in "Guide step", the file name, the prompt's date
  line and the duplicate check; in "Guides stay Markdown, with front matter", the example block and the `backfill`
  bullets (every guide rewritten and renamed, `generated` from the publish time instead of `null`, a guide with a
  problem of its own skipped instead of stopping the run); in "`site` command", the bullet "The index ... lists
  guides newest upload first". Also the `backfill.py` module docstring and the `backfill` command's docstring in
  `cli.py`, which say no guide is changed when one fails.
- [README.md](../README.md): `index.mjs` in "Where files go"; `node --test` under "Development";
  `file_name_timezone` in the config example; how guides are named and what `backfill` does now.
- [implementation_notes.md](implementation_notes.md): what the browser shows after deploying (the content type of
  `.mjs`), with the date and a command to recheck.
