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

### Livestream replays may be missing from `/videos` (not yet verified)

The `/membership` tab also listed roughly 3-hour `[MEMBERS] The Young Turks & Bonus Episode!` entries that did not
appear in the first `/videos` entries, and their `availability` was `None` despite being members-only. They are
probably livestream replays, which YouTube lists under `/streams`. Until this is checked, do not rely on `availability`
alone to detect members-only streams.

Recheck: compare `$Y --flat-playlist --playlist-end 10 -J <channel>/streams` with the `/membership` tab.

### Flat listings have no upload date

With `--flat-playlist`, `timestamp` and `upload_date` are empty. Entries come newest first, and `duration`,
`view_count` and `title` are present. Exact dates need a full (non-flat) extraction per video.

Recheck: `$Y --flat-playlist --playlist-end 3 -J https://www.youtube.com/@3blue1brown/videos`

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

### Auto-generated VTT subtitles repeat every line

A `en-orig.vtt` file from auto-generated captions has:

- A cue that shows the new line with word-level timing tags: `All<00:00:03.679><c> right.</c>`.
- A filler cue about 10 ms long (`00:00:05.670 --> 00:00:05.680`).
- The next cue repeating the previous line as plain text above the new one.

Joining cue text as-is roughly doubles the transcript. The parser must strip the tags, drop the filler cues and
remove the repeated lines.

Do not commit real subtitles from members-only videos as test fixtures. Use a short synthetic file with the same
structure.

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
