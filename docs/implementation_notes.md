# Implementation notes

How the external systems this tool depends on (YouTube, yt-dlp, Firefox, the `claude` CLI, Cloudflare) actually
behave, as found by testing. Any of it can change without notice, so every entry records when it was checked and how
to check it again. For what we decided to build, see [design.md](design.md).

Recheck commands use these shell variables:

```sh
Y='uvx --from yt-dlp[default,curl-cffi]@latest yt-dlp'
CK="firefox:$HOME/.mozilla/firefox/<profile folder>"   # a profile signed in as a channel member
```

Unless noted, entries were checked on 2026-10-09 with `@TheYoungTurks` (`UC1yBKRuGpC1tSM73A0ZjYjQ`), a channel the
user is a member of, and `@3blue1brown`.

## YouTube access

### Members-only videos are listed publicly but download only with a member's cookies

| Request | Without cookies | With a member's cookies |
| --- | --- | --- |
| `/videos` tab | Public and members-only videos mixed | Same |
| `UUMO<channel id without "UC">` playlist | Members-only videos | Same |
| `/membership` tab | `ERROR: This channel does not have a membership tab` | Lists members-only videos |
| Subtitles of a members-only video | `ERROR: Join this channel to get access to members-only content…` | Downloads |

- Members-only entries in flat listings have `availability: subscriber_only`. Public entries have `availability: None`.
- Consequence: listing needs no cookies. Cookies are needed only to fetch subtitles (or audio) of members-only videos.

Recheck:

```sh
$Y --flat-playlist --playlist-end 5 -J https://www.youtube.com/@TheYoungTurks/videos   # look for subscriber_only
$Y --write-auto-subs --sub-langs en-orig --skip-download https://www.youtube.com/watch?v=<members-only id>  # fails
$Y --cookies-from-browser "$CK" --write-auto-subs --sub-langs en-orig --skip-download <same URL>             # works
```

### Channel upload playlists split uploads by kind

Each channel has upload playlists named `<prefix><channel id without "UC">`
(`https://www.youtube.com/playlist?list=...`). Checked with `@TheYoungTurks` and `@3blue1brown`:

| Prefix | Contents | Shorts |
| --- | --- | --- |
| `UU` | All public uploads: videos and livestream replays | **Included** |
| `UULF` | Public long-form videos | No |
| `UULV` | Public livestream replays (`live_status: was_live`) | No |
| `UUMO` | All members-only uploads: videos and livestream replays | Not checked |
| `UUMF` | Members-only videos | No |
| `UUMV` | Members-only livestream replays | No |

- Public and members-only playlists do not overlap. `UULF`/`UULV` hold no members-only entries.
- Entries in `UUMF`/`UUMV` have `availability: subscriber_only`.
- A playlist the channel does not have (no livestreams, no memberships) fails with
  `ERROR: ... YouTube said: The playlist does not exist.` and exit status 1. Treat that as an empty list.
- Shorts cannot be filtered out of `UU`: their entries have normal `watch?v=` URLs, and durations (44 to 100 seconds
  seen) overlap with short regular videos. `list_latest_videos` therefore merges `UULF` + `UULV` (+ `UUMF` + `UUMV`).
- A livestream that is live right now appears in the `/streams` tab with `live_status: is_live` but was not in `UULV`.
  The listing drops `is_live` and `is_upcoming` entries anyway, since they have no finished subtitles.

Recheck:

```sh
ID=1yBKRuGpC1tSM73A0ZjYjQ   # @TheYoungTurks without "UC"
for P in UU UULF UULV UUMO UUMF UUMV; do
  $Y --flat-playlist --playlist-end 5 --print "$P %(id)s %(live_status)s %(availability)s %(title)s" \
    "https://www.youtube.com/playlist?list=$P$ID"
done
$Y --flat-playlist --playlist-end 5 --print id "https://www.youtube.com/playlist?list=UUSH$ID"  # Shorts; find them in UU
```

### The membership tab also links unlisted perk videos

The `/membership` tab (cookies needed) listed roughly 3-hour `[MEMBERS] The Young Turks & Bonus Episode!` replays
that appear in none of the upload playlists. A full extraction shows `availability: unlisted` and
`live_status: was_live`. They play without cookies, and had no `en-orig` automatic captions. They are left out of
listings for now.

Recheck: `$Y --skip-download --print "%(availability)s %(live_status)s" https://www.youtube.com/watch?v=ColFp8utld8`

### Flat listings have no upload date unless `approximate_date` is set

With `--flat-playlist`, `timestamp` and `upload_date` are empty. Entries come newest first, and `duration`,
`view_count` and `title` are present.

`--extractor-args youtubetab:approximate_date` fills `timestamp` from the "3 hours ago" text, for playlists
(`UU…`), but `upload_date` stays empty. Channel tabs (`/videos`, `/streams`) were not checked. Precision drops with
age: recent entries are accurate to the hour, while 3Blue1Brown's 2020 livestreams all share one timestamp. Use it to
interleave playlists, not to order entries within one. `merge_newest_first` keeps each playlist's own order.

Recheck:
`$Y --extractor-args youtubetab:approximate_date --flat-playlist --playlist-end 3 -J "https://www.youtube.com/playlist?list=UULF$ID"`

### Titles differ between listings

The same video can have different titles in different listings, and between runs. `bg4f_7W_Rr0` appeared as "How A
German Official Got CAUGHT Spying For Israel" in `/videos` and as "German Official BUSTED Spying For ISRAEL" in
`UULF`, probably because YouTube tests several titles. Identify videos by ID, never by title.

### Handles can point at unexpected channels

`@AIEngineer` resolves to an unrelated channel ("Fotoğraf Sanatçısı"). This is why `config add-channel` shows the
resolved name and asks for confirmation.

### Channel root URLs give the channel name and ID in one call

`$Y --flat-playlist --playlist-items 1 -J https://www.youtube.com/@handle` returns `channel`, `channel_id` and
`channel_url` at the top level. `resolve_channel` relies on this.

### The embedded player refuses `file://` pages (Error 153)

Checked on 2026-10-09 with headless Google Chrome and the HTML guide page template
(`src/ces_youtube_tools/templates/guide_page.html`), embedding `aircAruvnKk` (3Blue1Brown, public).

| Page opened from | Result |
| --- | --- |
| `file:///.../preview.html` | "Video player configuration error", Error 153. The IFrame API's `onError` fires. |
| `http://127.0.0.1:8765/preview.html` (`python -m http.server`) | The player loads normally. |

- `referrerpolicy="strict-origin-when-cross-origin"` on the `<iframe>` does not help for `file://`: the page has no
  origin to send as a `Referer`.
- On a `file://` page the IFrame API fires `onReady` first, then `onError` with `event.data === 153`. A page can
  check `location.protocol === "file:"` up front instead of waiting for the error.
- A video ID that does not exist, served from `localhost`, gives an `onError` code in the 100/101/150 group (the
  page treats them the same, so the exact code was not recorded).
- Confirmed by the user in a browser on 2026-10-09: Error 153 goes away when the page comes from a local HTTP server.
- Consequence: an HTML guide must be served over HTTP, even from `localhost`, for the player to work. Opened from
  disk, it is still readable and its timestamp links open YouTube.
- Firefox: not yet verified (headless Firefox did not start while the user's Firefox was running).
- Members-only videos in the embedded player: on 2026-10-09 the user (Firefox, signed in as a member, page served
  from `localhost`) saw YouTube's join prompt inside the player ("Members-only content. Become a member of this
  channel to watch...") and no `onError`. Turning off Enhanced Tracking Protection for the page (the shield icon in
  the address bar) made the video play. So the embed works, but the sign-in cookies are blocked for the third-party
  iframe; the viewer is treated as a non-member, which is a valid state and not an error. The page cannot detect it:
  headless Chrome (signed out) gave the same events (`onReady`, state 3, then -1) for a members-only and a public
  video, and `getVideoData().title` and `getDuration()` are filled in for both. Opening
  `https://www.youtube.com/embed/<video id>` directly is no test: it sends no referrer, so even a public video gives
  Error 153 (checked by the user). Chrome with third-party cookies blocked: not yet verified. Recheck: serve a page
  for a members-only video, then compare with tracking protection on and off for that site.
  A full extraction of a members-only video also reports `availability: subscriber_only` (confirmed by the user on
  2026-10-09, through the hint that `guide --html` adds to the page for such videos).
- The `serve` and `html` commands built on this check were removed on 2026-10-20; the site on Cloudflare Pages
  replaces them. Whether the player works on the deployed pages is tracked under "Cloudflare Pages and Access" below.

Recheck: write a page with the template, then compare
`google-chrome --headless=new --virtual-time-budget=6000 --screenshot=file.png file://$PWD/page.html` with the same
command on `http://127.0.0.1:8765/page.html` while `uv run python -m http.server 8765 --bind 127.0.0.1` runs in that
folder.

## yt-dlp

### `--print` applies to each entry, not to the playlist

`$Y --flat-playlist --print channel_id <channel URL>` printed `NA`, because the field is read from each entry (here,
the channel's tabs). Read playlist-level fields from `-J` output instead.

### Automatic captions repeat every line, human-made subtitles do not

Checked on 2026-10-09 with `3i2C3IB3PaA` (TYT, members-only, automatic `en-orig`) and `GlYgs6v2YfU` (3Blue1Brown,
human-made `en`).

**Automatic captions roll.** The 13.5-minute `en-orig.vtt` file had 725 cues of three kinds:

| Cue | Count | Lines | Example |
| --- | --- | --- | --- |
| New text with word timing | 352 | Previous line (plain), new line with `<00:00:05.920><c> will</c>` tags | `All right. Uh,` / `this<00:00:05.920><c> will</c>...` |
| Filler, about 10 ms | 362 | The line just shown, then a line holding one space | `00:00:05.670 --> 00:00:05.680` |
| New text without timing | 11 | Previous line, short final word(s) with no tags | `could make sure that they served Israel` / `100%.` |

- The first cue's previous-line slot is a line holding one space, and so are many lines in filler cues. **Only
  truly empty lines separate cues.** Splitting on whitespace-only lines tears cues apart.
- Text contains HTML entities: `&gt;&gt;` is YouTube's speaker-change marker (`>>`). Sound tags look like `[music]`,
  `[snorts]`.
- Joining all cue text as-is roughly doubles the transcript.

**Human-made subtitles** (576 cues) have one or more lines per cue, no timing tags and no repeats. Cues may have
numeric identifiers, and timestamps may be `MM:SS.mmm`.

**One rule handles both** (`vtt.parse_vtt`): strip tags, decode entities, drop empty lines, then drop the lines at the
start of a cue that equal the line emitted last, and keep the rest. On the files above: 725 cues gave 363 segments,
2,444 words (about 180 words per minute) with no repeated text, and all 576 human-made cues were kept. A rule like
"take each cue's last line" would have dropped text from multi-line human-made cues.

Do not commit real subtitles from members-only videos as test fixtures. Use short synthetic text with the same
structure. Build the space-only lines in code (`tests/test_vtt.py`), because the `trailing-whitespace` pre-commit
hook would turn them into empty lines in a fixture file.

Recheck:
`$Y --cookies-from-browser "$CK" --write-auto-subs --sub-langs en-orig --skip-download -o "%(id)s.%(ext)s" https://www.youtube.com/watch?v=3i2C3IB3PaA`,
then inspect the cues, for example with `grep -c -- '-->' 3i2C3IB3PaA.en-orig.vtt`.

### Video metadata for the transcript step

Checked on 2026-10-09 with `$Y -J --skip-download <video URL>` (with cookies for the members-only video).

- **Members-only videos need cookies for the full extraction too**, not only for the subtitle download.
- `subtitles` lists human-made tracks (`en`, `es`, ...). For livestream replays it can also hold `live_chat`, which is
  the chat replay, not subtitles. `automatic_captions` lists about 157 languages: `en-orig` is the original speech
  recognition track and the others are machine translations.
- Each track is offered in several formats (`json3`, `srv1`-`srv3`, `ttml`, `srt`, `vtt`). The provider requests
  `--sub-format vtt`.
- `chapters` (`start_time`, `end_time`, `title`) are present when the uploader set them. `GlYgs6v2YfU` had 10; the
  members-only TYT video had none.
- The output (about 580 KB, mostly format and caption URLs) contained no session cookies when run with cookies
  (searched for `SAPISID`, `__Secure-3PSID`, `LOGIN_INFO`, `cookies`, `Cookie`). The cache stores only the
  `VideoInfo` fields anyway, as `video.json`.
- `--load-info-json` cannot download subtitles from a saved file whose `formats` were removed (`ERROR: No video
  formats found!`), so the provider runs a second yt-dlp call for the chosen track instead of reusing saved output.
  That costs one extra extraction (a few seconds) the first time; afterwards the transcript is cached.

Recheck: `$Y -J --skip-download https://www.youtube.com/watch?v=GlYgs6v2YfU | python3 -c "import json,sys; d=json.load(sys.stdin); print(sorted(d['subtitles']), len(d['automatic_captions']), len(d.get('chapters') or []))"`

### Upload dates are UTC days; exact times are in `timestamp` and `release_timestamp`

Checked on 2026-10-21 with yt-dlp 2026.08.19 and cookies, on three David Pakman and Luke Beasley videos and one
members-only TYT video:

| Video | `live_status` | `upload_date` | `timestamp` (UTC) | `release_timestamp` (UTC) | Asia/Taipei day |
| --- | --- | --- | --- | --- | --- |
| `VtuqHVL6dPk` | `was_live` | 20261007 | 2026-10-07 20:31 | 2026-10-07 18:57 | 20261008 |
| `7SF8GJT0pxo` | `not_live` | 20261014 | 2026-10-14 19:00 | none | 20261015 |
| `REttiUk0ihw` | `not_live` | 20261016 | 2026-10-16 19:00 | none | 20261017 |
| `aA8hOuWCJvQ` | `not_live` (members-only) | 20261017 | 2026-10-17 01:51 | none | 20261017 |

- Every video had `timestamp`, to the second, members-only included. `upload_date` was its UTC day, which matches
  yt-dlp's source: it takes the day from `timestamp` in UTC when YouTube gives a time with a time zone.
- US afternoon uploads fall on the next day in Asia/Taipei, so the UTC `upload_date` reads a day early there; that is
  the "off" date seen in guide file names.
- For the stream replay, `release_timestamp` was when the stream started, and `timestamp` was 5644 seconds later,
  close to the stream's length (5369 seconds) plus a few minutes: about when the stream ended, not when it was
  scheduled. Both are on the same UTC day here; a stream across midnight UTC would differ.
- Not yet verified: premieres, scheduled streams, and videos whose `timestamp` is missing (yt-dlp then falls back to
  a date-only `uploadDate`, in US/Pacific per its source comment, or the page's date text).
- Without cookies, the same calls failed with "Sign in to confirm you're not a bot".

Recheck:

```sh
$Y --cookies-from-browser "$CK" --skip-download --print "%(id)s %(live_status)s %(upload_date)s %(timestamp)s %(release_timestamp)s" VtuqHVL6dPk 7SF8GJT0pxo REttiUk0ihw aA8hOuWCJvQ
```

### The default command uses `@latest`

`uvx --from "yt-dlp[default,curl-cffi]@latest" yt-dlp` picks up new yt-dlp releases, which matters because YouTube
changes often break older releases. Recent releases may also need a JavaScript runtime such as `deno` for some
YouTube downloads. Check that first when downloads fail with cookies that `config check` accepts.

## Firefox profiles

### `profiles.ini` lists only one member of each profile group

The profile manager added in 2025 groups profiles. `profiles.ini` holds one entry per group (it has a `StoreID=` line)
pointing at whichever member was used last, plus standalone profiles. Every group member and its display name is in
`Profile Groups/<StoreID>.sqlite`, table `Profiles(path, name)`. The `Name=` in `profiles.ini` can be stale (the
"YouTube Turk" profile was listed as `default-release`).

Discovery must read both sources. Reading only `profiles.ini` hid the "YouTube Turk" profile after the user switched
back to the original profile.

Recheck: `cat ~/.mozilla/firefox/profiles.ini` after switching profiles in Firefox, and compare it with
`SELECT path, name FROM Profiles` in the `Profile Groups` database.

### yt-dlp needs the profile folder path

`yt-dlp --help` describes the Firefox `PROFILE` value as a "name/path", meaning the profile's folder. Firefox display
names live only in Firefox's own records, which yt-dlp does not read. Passing the absolute folder path works (verified).
A display name was not tried. Store and pass the folder path.

### Copy the `-wal` file along with the database

Firefox writes recent changes to `cookies.sqlite-wal` (and `*.sqlite-wal` in `Profile Groups`) first. Reading a copy
of the `.sqlite` file alone can miss a fresh sign-in. Copying also avoids touching the files Firefox has open.

### Firefox stores cookies unencrypted on Linux

yt-dlp reads Firefox cookies without the system keyring, unlike Chromium-based browsers.

### Sign-in detection

- Local check: the `.youtube.com` cookies include `SAPISID` and `LOGIN_INFO` (only names are read). A signed-out
  profile that has visited YouTube has only `VISITOR_INFO1_LIVE`, `PREF`, `NID` and similar.
- Live check: `https://www.youtube.com/feed/subscriptions` only works when signed in, so a successful flat listing of it
  confirms YouTube accepts the cookies.

### Google's account menu does not isolate accounts

When several Google accounts are signed in to one browser profile, the cookies cover all of them and the active
account is chosen per request. A cookie snapshot cannot select one account. Use a dedicated Firefox profile signed in
to a single account.

### Making a new profile changes Firefox's default

After creating "YouTube Turk", `installs.ini` and `profiles.ini` set it as the install's default profile, so launching
Firefox normally may open it. Set the default back in `about:profiles` if that is unwanted.

## Typer

### Help text is the whole docstring

Checked on 2026-10-09 with typer 0.27. A command without `help=` shows its entire docstring in `--help`, including
Google-style `Args:`, `Returns:` and `Raises:` sections. Typer does not parse docstrings. `cli.command()` passes
`help=` with the text before the first such section.

Recheck: register a command with `@APP.command("x")` and a Google-style docstring, then run
`ces-youtube-tools x --help`.

## claude CLI

Checked with Claude Code 2.1.295 (`claude --help`).

- `-p` / `--print` runs non-interactively and prints the reply.
- `--tools ""` disables all built-in tools.
- `--no-session-persistence` keeps runs out of saved sessions.
- `--output-format text` (default), `json` or `stream-json`.
- Pass the prompt on stdin. A long transcript is too large to pass safely as an argument.
- `--system-prompt <prompt>` replaces the default (coding-agent) system prompt. `--effort <level>` takes `low`,
  `medium`, `high`, `xhigh` or `max`.
- `--bare` skips hooks, plugins, auto-memory and CLAUDE.md auto-discovery, but then "Anthropic auth is strictly
  ANTHROPIC_API_KEY or apiKeyHelper via --settings (OAuth and keychain are never read)". It breaks a Claude
  subscription login, so the generator only passes it when `bare = true`. Without it, `claude` reads `CLAUDE.md`
  files, which the generator avoids with `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1` (see below). User-level settings in
  `~/.claude` are expected to still apply (not tested).

### Ignoring CLAUDE.md files

Checked on 2026-10-09 with Claude Code 2.1.295.

- Setting `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1` in the environment makes `claude -p` ignore CLAUDE.md files. With a
  `CLAUDE.md` saying "The secret codeword is PINEAPPLE42" in the working directory, asking for the codeword returned
  `PINEAPPLE42` by default and `NONE` with the variable set. Unlike `--bare`, it keeps the subscription login.
- The variable is not in `claude --help`. It was found in the binary's strings, so it may change between versions.
- Whether it also disables the user-level `~/.claude/CLAUDE.md` is not yet verified (the file on the test machine was
  empty).

Recheck: write a `CLAUDE.md` with a made-up codeword in an empty directory, then run
`echo "Reply with the secret codeword from your instructions, or NONE." | CLAUDE_CODE_DISABLE_CLAUDE_MDS=1 claude -p --tools "" --no-session-persistence`
there. It should print `NONE`. Without the variable it should print the codeword.

### Session logs

Checked on 2026-10-09 with Claude Code 2.1.295.

- A session log is written to `~/.claude/projects/<cwd>/<session uuid>.jsonl`. `<cwd>` is the working directory with
  every non-alphanumeric character replaced by `-` (`/tmp/x/no-flag` becomes `-tmp-x-no-flag`). Different paths can
  therefore share one folder name.
- With `--no-session-persistence` no `.jsonl` file is written. With Claude Code's default system prompt the project
  folder and an empty `memory/` folder inside it are still created. With `--system-prompt` (what the generator
  passes) no folder is created at all. `CLAUDE_CODE_DISABLE_CLAUDE_MDS` makes no difference to this.
- `claude --help` has no option for the log location (`--debug-file` only moves debug logs). The environment variable
  `CLAUDE_CONFIG_DIR` moves the whole tree (`<dir>/projects/...`), but it also moves credentials and settings: a run
  with a fresh `CLAUDE_CONFIG_DIR` printed `Not logged in`. The generator therefore sets the working directory instead.
- `--name <name>` is stored in the log as a `{"type":"custom-title","customTitle":"<name>"}` record. The log also has
  `usage` entries, which is what usage tracking would read.

Recheck: run `echo hi | claude -p --tools "" --name test-name` in a new empty directory `D`, then
`grep custom-title ~/.claude/projects/*<D's name>/*.jsonl`. Repeat with `--no-session-persistence` (and with
`--system-prompt "x"`) and check that no `.jsonl` file appears. Delete the project folders afterwards.

### Prompt caching (`claude-cli`)

Checked on 2026-10-09 with `--model sonnet` and a one-word prompt.

- With `DISABLE_PROMPT_CACHING=1`, the session log shows `cache_creation_input_tokens` of 0 on both requests. Without
  it, both show 3593. So the variable works, and the per-model variants (`DISABLE_PROMPT_CACHING_HAIKU`, `_SONNET`,
  `_OPUS`) are not needed. They were not tested.

Recheck: in a new empty directory `D`, run `echo hi | claude -p --tools "" --name cache-off` with and without
`DISABLE_PROMPT_CACHING=1`, then compare `grep -o '"cache_creation_input_tokens":[0-9]*' ~/.claude/projects/*<D's name>/*.jsonl`.
Delete the project folder afterwards.

### Guide generation runs (`claude-cli`)

Checked on 2026-10-09 with the default template and Claude Code's default model, on `3i2C3IB3PaA` (13.5 minutes,
automatic captions, 363 segments).

| Transcript blocks | Time | Guide | Timestamp links | Labels corrected |
| --- | --- | --- | --- | --- |
| 30 s (26 blocks) | 55 s | 9 KB, 6 segments, one table | 38 | 0 |
| 15 s (51 blocks) | 41 s | similar | 43 | 0 |

- The model followed the requested structure and did not wrap the reply in a code fence. `clean_output` still
  removes one if it appears.
- With 30-second blocks every highlight landed on a block start (0:01, 0:31, 1:02, ...). With 15-second blocks the
  highlights spread out (0:17, 0:33, 3:59, ...), closer to the hand-written sample.
- A speech recognition error ("chill in grease", meaning Greece) was copied into a quote while the summary spelled it
  correctly, because "quote exactly" won over "fix misspellings". Saying that misspelling fixes apply to quotes too
  fixed it on the next run.
- No label needed correcting in either run, but labels are still rewritten from the `t=` seconds, since that is
  cheap and errors are more likely past one hour.

Recheck: `ces-youtube-tools guide 3i2C3IB3PaA -o /tmp/guides --force`, then compare the guide and the counts printed.

### Claude cannot transcribe audio

Checked on 2026-10-09 against the Claude API reference bundled with Claude Code 2.1.295 (the `claude-api` skill).

- Claude accepts text, images and documents (PDF or plain text) as input. There is no audio content block, so neither
  the API nor `claude -p` can do speech recognition. The reference mentions audio only as an unsupported content type
  (`UnsupportedMCPValueError` when converting MCP values).
- With tools enabled, `claude` could run a local ASR program through Bash, but the program does the transcription.
  The pipeline calls ASR directly instead and keeps `--tools ""` for the guide step.
- Consequence: an audio transcript provider needs a separate ASR backend. It downloads audio with yt-dlp (with cookies
  for members-only videos) and returns the same `Transcript` segments as the subtitle provider.

Recheck: the input content types in the Messages API documentation (`shared/live-sources.md` in the `claude-api`
skill), or `client.models.retrieve(<model>).capabilities` from the Models API.

## Cloudflare Pages and Access

### Access covers preview deployments through the wildcard host name

Checked on 2026-10-20 with `curl` (no browser session) against the project `ces-youtube-watching-guides`, after its
first deployment (`wrangler` 4.151.3, built from commit `26bfc93`). The Access application lists
`ces-youtube-watching-guides.pages.dev` and `*.ces-youtube-watching-guides.pages.dev`.

| Host name | Result |
| --- | --- |
| `ces-youtube-watching-guides.pages.dev` (production) | `HTTP/2 302` to `https://ceshine.cloudflareaccess.com/cdn-cgi/access/login/ces-youtube-watching-guides.pages.dev?...` |
| `77bf24d5.ces-youtube-watching-guides.pages.dev` (the deployment's own URL) | `HTTP/2 302` to the same login, naming `77bf24d5.ces-youtube-watching-guides.pages.dev` |

- The deployment host name was never added to Access by name, so the wildcard entry is what covers it. Every
  deployment gets a new `<hash>` host name, and earlier deployments keep serving their files there, so the wildcard
  entry is required.
- The login URL's `meta` token (a JWT) names the requested host name and has `"auth_status":"NONE"`, so the redirect
  comes from Access and not from the site.
- `wrangler pages deployment list` shows the git commit a deployment was made from (`Source`).
- Not yet verified: that `_headers` is applied (`X-Robots-Tag`, `Referrer-Policy`), since unauthenticated requests
  never reach the site; and that the embedded player works on the deployed pages (it needs the page's origin as the
  `Referer`, see "The embedded player refuses `file://` pages"). Recheck, signed in through Access in a browser: open
  a guide page, check the response headers in the developer tools, and click a timestamp.

Recheck the redirects:

```sh
curl -sI https://ces-youtube-watching-guides.pages.dev/ | grep -i -E '^(HTTP|location)'
npx wrangler pages deployment list --project-name ces-youtube-watching-guides   # lists the <hash> host names
curl -sI https://<hash>.ces-youtube-watching-guides.pages.dev/ | grep -i -E '^(HTTP|location)'
```

### Pages serves `/<video_id>` from `<video_id>.html`

Checked on 2026-10-21 by the owner, in a browser signed in through Access, on the production host name
(`ces-youtube-watching-guides.pages.dev`): `/<video_id>` serves the page `<video_id>.html`. So extensionless links
work on Pages.

- Not yet verified: that `/<video_id>.html` redirects (308) to `/<video_id>`; that `./`, the guide pages' "All
  guides" link, serves `index.html`; that `npx wrangler pages dev` serves extensionless names the same way.
- Recheck, signed in through Access in a browser: open `https://ces-youtube-watching-guides.pages.dev/<video_id>` for
  a published guide, then click "All guides" and check that the index loads. `curl` cannot check it, because Access
  answers requests without a session with its login redirect.
