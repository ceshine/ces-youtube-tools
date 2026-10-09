# Implementation notes

How the external systems this tool depends on (YouTube, yt-dlp, Firefox, the `claude` CLI) actually behave, as
found by testing. Any of it can change without notice, so every entry records when it was checked and how to check it
again. For what we decided to build, see [design.md](design.md).

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
  subscription login, so the generator only passes it when `bare = true`. Without it, `claude` would read `CLAUDE.md`
  files for its working directory, which is why the generator runs in the video's work directory. User-level
  settings in `~/.claude` are expected to still apply (not tested).

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

Recheck: `youtube-tools guide 3i2C3IB3PaA -o /tmp/guides --force`, then compare the guide and the counts printed.

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
