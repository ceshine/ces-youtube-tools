# ces-youtube-tools

Browse a YouTube channel's latest videos, pick one, and generate a Markdown **watching guide** from its transcript: an
overview, then numbered segments with timestamped highlights that link back into the video.

Members-only videos work too, if you are a member of the channel.

> **About this project**
>
> This codebase was mostly created by **Claude Opus 5.5**, working under the guidelines in [AGENTS.md](AGENTS.md).
> This README was written by **Claude Sonnet 5.5**. Review the code before you rely on it.

## How it works

```
ces-youtube-tools browse
  1. pick a channel from your config
  2. list its latest videos (yt-dlp, no cookies)  ─► pick one
  3. fetch subtitles  ─►  build a prompt  ─►  `claude` CLI  ─►  "Watching Guide <title>.md"
```

- **Listing** uses yt-dlp on the channel's upload playlists, without cookies.
- **Transcripts** come from the video's own subtitles (human-made first, then automatic captions). Cookies are read
  live from a dedicated Firefox profile, which is only needed for members-only videos.
- **Guides** are written by the [`claude` CLI](https://docs.claude.com/en/docs/claude-code) with all tools disabled,
  so the model only writes text. The prompt template is a file you can edit.

## Requirements

> **Platform:** Only Linux is targeted at the moment. Other systems (macOS, Windows) are untested, and the Firefox
> profile paths below are Linux paths.

- Python 3.13 or newer and [uv](https://docs.astral.sh/uv/)
- [Claude Code](https://docs.claude.com/en/docs/claude-code) (the `claude` command), signed in
- Firefox, with a profile signed in to YouTube (only for members-only videos)
- yt-dlp is run through `uvx` by default, so you do not need to install it. `ytdlp_command` in the config changes
  this.

## Install

```bash
uv sync --frozen
uv run ces-youtube-tools --help
```

Or install the command on your `PATH`:

```bash
uv tool install .
```

## Quick start

```bash
# 1. Create the config and pick the Firefox profile used for YouTube cookies
ces-youtube-tools config init

# 2. Add the channels you follow (--members also lists members-only videos)
ces-youtube-tools config add-channel @3blue1brown
ces-youtube-tools config add-channel @SomeChannel --members

# 3. Check that the profile is signed in (optional)
ces-youtube-tools config check

# 4. Pick a channel and a video, and write the guide
ces-youtube-tools browse
```

## Commands

| Command | What it does |
| --- | --- |
| `browse [CHANNEL] [-n N] [-o DIR] [--refresh] [--html]` | Pick a channel and a video from numbered menus, then write its guide |
| `list CHANNEL [-n N]` | List a configured channel's latest videos and livestream replays |
| `transcript VIDEO [--json] [--refresh]` | Print a video's transcript to stdout |
| `guide VIDEO [-o DIR] [--force] [--refresh] [--html]` | Write a guide for a video URL or ID |
| `html PATH... [--overwrite]` | Turn Markdown guides (files or folders) into HTML pages with the video embedded |
| `serve [DIR] [--port N] [--no-open]` | Serve a folder of HTML pages on localhost, so their players work |
| `config init` | Create the config file and choose a Firefox profile |
| `config profile` | Choose a different Firefox profile |
| `config add-channel CHANNEL [--members] [-y]` | Look up a channel and add it |
| `config remove-channel CHANNEL` | Remove a channel |
| `config show` | Print the config and whether the profile has YouTube sign-in cookies |
| `config check` | Check with YouTube that the profile is signed in |
| `config template [--force]` | Write the built-in prompt template to the configured path, for editing |

Every command takes `--config PATH` to use a different config file. In the menus, type a number to choose, press Enter
for the default, or type `q` to quit.

## HTML pages

A guide can also be an HTML page that embeds the video's player. Clicking a timestamp moves the player to that moment,
so you read and watch on one page.

```bash
ces-youtube-tools browse --html               # write the guide and its page
ces-youtube-tools html ~/guides               # add pages to guides you already have
ces-youtube-tools serve ~/guides              # open them in the browser
```

- `html` takes Markdown files and folders (the `.md` files directly inside a folder). Each page is written next to its
  guide. An existing page is kept with a warning unless you pass `--overwrite`. When `guide --html` or
  `browse --html` writes a guide, it replaces that guide's page too.
- YouTube refuses to play an embedded video on a page opened from disk (Error 153), so use `serve`. It listens on
  `127.0.0.1` only. Opened from disk, a page is still a readable guide whose timestamps open YouTube.
- A page needs a network connection for the player and fonts.
- **Members-only videos:** the player shows a "become a member" prompt unless your browser sends your YouTube sign-in
  to the embedded player. In Firefox, turn off Enhanced Tracking Protection for the page (the shield icon in the
  address bar); Firefox keeps the setting for `localhost`. Pages written by `guide --html` and `browse --html` for a
  members-only video say this under the player. Chrome may need third-party cookies allowed for `localhost`
  (not yet verified). Without that, the timestamps still work as links to YouTube in a new tab (Ctrl+click).

## Configuration

The config lives at `~/.config/ces-youtube-tools/config.toml`:

```toml
ytdlp_command = ["uvx", "--from", "yt-dlp[default,curl-cffi]@latest", "yt-dlp"]
cache_dir = "~/.cache/ces-youtube-tools"

[auth]
firefox_profile = "/home/<user>/.mozilla/firefox/xxxxxxxx.Profile 1"

[[channels]]
name = "3Blue1Brown"
url = "https://www.youtube.com/@3blue1brown"
channel_id = "UCYO_jab_esuFRV4b17AJtAw"
members = false

[transcript]
languages = ["en-orig", "en"]

[guide]
prompt_template = "~/.config/ces-youtube-tools/guide_prompt.md"
# output_dir defaults to the directory you run the command from

[guide.options.claude-cli]
model = "opus"       # optional
effort = "high"      # optional
```

Edit `guide_prompt.md` to change the structure and tone of the guides. Run `ces-youtube-tools config show` to see every
setting, including the optional ones such as `session_dir`.

Guide generation turns off Claude Code's prompt caching, because each run is a single request that is never repeated
and a cache write only adds cost. Set `prompt_caching = true` under `[guide.options.claude-cli]` to turn it back on.

## Where files go

- **Guides:** `Watching Guide <title>.md` in `--output-dir`, else `guide.output_dir`, else the current directory. An
  existing guide is not overwritten unless you pass `--force`.
- **HTML pages:** `<same name>.html`, next to each guide.
- **Cache:** `~/.cache/ces-youtube-tools/<video id>/` holds the video metadata, subtitles, transcript, and the prompt
  and raw reply of the last generation. A rerun repeats only the failed step; `--refresh` ignores the cache.

## Development

```bash
uv run pytest                          # tests
uvx ruff check .                       # lint
uvx ruff format --line-length 120      # format
```

Tests mock yt-dlp, the network, the `claude` CLI and Firefox profiles. The only subtitle fixture is a short synthetic
file.

## License

[MIT](LICENSE)

## Documentation

- [docs/design.md](docs/design.md): what was built, and why
- [docs/implementation_notes.md](docs/implementation_notes.md): observed behavior of YouTube, yt-dlp, Firefox and the
  `claude` CLI, with commands to recheck it
- [docs/asr_options.md](docs/asr_options.md): speech recognition options for videos without subtitles (planned)
- [AGENTS.md](AGENTS.md): guidelines for AI coding agents working on this repository
