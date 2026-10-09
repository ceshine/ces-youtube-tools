# youtube-tools design

Browse the latest videos of a YouTube channel (including members-only videos when the user is a member), pick one,
and generate a Markdown watching guide from its transcript.

Observed behavior of YouTube, yt-dlp, Firefox and the `claude` CLI, with recheck commands, is in
[implementation_notes.md](implementation_notes.md).

## Workflow

```
 youtube-tools config ...          (run once, edit occasionally)
   ├─ pick a Firefox profile  ──┐
   └─ add/remove channels     ──┴──► ~/.config/youtube-tools/config.toml

 youtube-tools browse [CHANNEL]    (everyday command)
   1. pick a channel from the config
   2. list its latest videos (yt-dlp, no cookies needed) ─► pick one
   3. run the guide pipeline

 youtube-tools guide URL           (same pipeline, no browsing)

 guide pipeline
   VideoInfo ─► TranscriptProvider ─► Transcript ─► GuideGenerator ─► Markdown file
                 · yt-dlp-subs (v1)                  · claude-cli (v1)
                 · audio transcription (later)       · openai-api, other CLIs (later)
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

`~/.config/youtube-tools/config.toml`, read with `tomllib`, written with `tomli-w`, and validated with Pydantic.

```toml
ytdlp_command = ["uvx", "--from", "yt-dlp[default,curl-cffi]@latest", "yt-dlp"]

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
prompt_template = "~/.config/youtube-tools/guide_prompt.md"
# output_dir defaults to the directory the command is run from

[guide.options.claude-cli]   # per-generator settings, keyed by generator name
model = "opus"
```

- `ytdlp_command` defaults to `uvx ... @latest` because YouTube changes often break older yt-dlp releases.
- `channel_id` is resolved once by `config add-channel`. It is the stable key for a channel (handles can change) and
  gives the `UUMO<id without "UC">` members-only playlist if that is ever needed.
- `members` decides whether the listing includes members-only uploads (the `UUMF` and `UUMV` playlists). Leave it
  off for channels you are not a member of, because their members-only videos cannot be downloaded.
- `prompt_template` is a user-editable file. A default template ships with the package and is copied there on
  `config init`.

## Pipeline interfaces

```python
class TranscriptProvider(Protocol):
    def fetch(self, video: VideoInfo, work_dir: Path) -> Transcript: ...


class GuideGenerator(Protocol):
    def generate(self, video: VideoInfo, transcript: Transcript) -> str: ...  # Markdown
```

- `Transcript` is a list of timestamped segments (`start`, `end`, `text`). The yt-dlp provider removes the rolling
  duplicate lines from YouTube's auto-generated VTT subtitles. Generators never see raw subtitle files.
- Prompt building is shared: the template plus `VideoInfo` and `Transcript` produce the prompt. Generators only send
  the prompt and return the reply.
- Implementations are chosen by name from a plain dict registry keyed by the config values.

### claude-cli generator

```
claude -p --tools "" --no-session-persistence --output-format text [--model MODEL] < prompt
```

- `--tools ""` disables all tools. The model only writes text.
- The prompt goes in on stdin, because transcripts are too long for a command-line argument.

## Work directory

`~/.cache/youtube-tools/<video_id>/` holds `info.json`, the raw subtitle file and `transcript.json`. A rerun with a
different generator reuses them, and a failed step is the only one repeated.

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

## Build order

1. Config: models, load/save, Firefox profile discovery and picker, `config` commands. (Done.)
2. Video listing: `youtube-tools list CHANNEL`. (Done.) See "Video listing" below.
3. Transcript: yt-dlp subtitle provider, VTT parser with duplicate removal, work-directory cache.
4. Guide: prompt template and builder, `ClaudeCliGenerator`, `guide URL` command.
5. `browse`: interactive channel and video menus on top of steps 1–4. Choose the prompt library here, because the
   interactive config session below uses the same one.

## Planned: interactive config session

Not needed for the first version. The `config` subcommands stay as they are, for scripting.

`youtube-tools config` with no subcommand starts a session:

```
$ youtube-tools config
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
Saved ~/.config/youtube-tools/config.toml
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
