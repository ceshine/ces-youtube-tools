# The `pi` generator: implementation plan

Written on 2026-11-07. A third guide generator, `pi-cli`, writes guides with the Pi coding agent (`pi`) in JSON
mode, as `claude-cli` does with Claude Code and `agy-cli` with Antigravity CLI. It registers a `GeneratorSpec` and
sends the prompt with `run_prompt_command`, like the other two, and adds item 15 to "Build order" in
[design.md](design.md).

## Decisions

Settled on 2026-11-07 by running `pi` 1.5.0 (`@earendil-works/pi-coding-agent`) with `openai/gpt-6.1-terra`. The
test runs are described under each decision.

- **The prompt goes to stdin.** `pi` prepends piped stdin to the first prompt, and with no message argument the
  stdin is the whole prompt: `echo "Reply with exactly: hello from stdin" | pi -p ...` printed `hello from stdin`.
  A 189 KB prompt on stdin worked, and so did the largest saved guide prompt (161 KB,
  `~/.cache/ces-youtube-tools/pHiM7g3u2A8/prompt.md`). So, unlike `agy`, no wrapping is needed and the 128 KiB
  argument limit does not apply.
- **Output is JSON mode, not text mode.** `--print` (text mode) prints only the final text, and exits non-zero on an
  `error`, `aborted` or `length` stop reason (`--model openai/not-a-model` exited 1 with the OpenAI 404 on stderr
  and nothing on stdout). That would be enough for `run_prompt_command`, but text mode cannot show which model and
  thinking level ran, and the next two decisions need that. `--mode json` prints a `session` header and one event
  per line; the `message_end` event of the assistant message holds `provider`, `model`, `thinkingLevel`,
  `stopReason`, `errorMessage` and the content blocks. In JSON mode a failed reply exits 0 (the same 404 run exited 0
  with `stopReason: "error"`), so the generator checks `stopReason` itself. `--mode json` also runs once and exits,
  so `--print` is not passed.
- **`model` must be `provider/id`, and the model that ran is checked.** `--model` matches fuzzily and ignores case,
  with no warning:

  | `--model`              | Ran                                  |
  |------------------------|--------------------------------------|
  | `openai/gpt-6.1-terr`  | `openai` `gpt-6.1-terra`             |
  | `GPT-6.1-Terra`        | `openai` `gpt-6.1-terra`             |
  | `terra`                | `openrouter` `openai/gpt-6.1-terra-pro` |
  | `not-a-model`          | nothing: exit 1, "Model "not-a-model" not found" |
  | `openai/not-a-model`   | sent as a custom ID (warning on stderr), OpenAI 404 |

  A provider prefix keeps the match on that provider, so `PiCliOptions.model` must have the form `provider/id`. Nested
  IDs work: `openrouter/openai/gpt-6.1-terra` ran as provider `openrouter`, model `openai/gpt-6.1-terra`. The prefix
  does not stop a typo matching another model of the same provider, so after the run the generator compares
  `<provider>/<model>` from the assistant message with the `model` option and raises `GuideError` if they differ,
  telling the user to copy the exact ID from `pi --list-models`. The front matter then always records the model that
  ran. The check comes after the run, so a typo costs one generation; `config show` only validates options and runs
  nothing (see "config show" in [design.md](design.md)), so it cannot catch this earlier. `generate` does not check
  the ID before the run with `pi --list-models <id>` either: that listing is a fuzzy search too, and prints a table
  for people to read, so the generator would have to parse its columns. The check after the run reads `pi`'s own
  JSON.
- **`effort` is passed as `--thinking`, and the level that ran is checked.** `pi` clamps a level the model does not
  support, with no warning: `--thinking minimal` ran as `low` on `gpt-6.1-terra`. An unknown level (`--thinking
  bogus`) printed a warning and ran at `high`, the user's saved `defaultThinkingLevel`. Without `--thinking` the
  saved default applies too. So `effort` is always passed, is a `Literal` of the levels `pi --help` lists (`off`,
  `minimal`, `low`, `medium`, `high`, `xhigh`, `max`), and after the run `thinkingLevel` in the assistant message
  must equal it, or the run fails with a message saying the model may not support that level. `thinkingLevel` is
  optional in `pi`'s message types; if it is missing, the run fails with "pi did not report the thinking level", not
  with the message about support. Every level except
  `minimal` ran as given on `gpt-6.1-terra`. A `:<level>` suffix on the model (`openai/gpt-6.1-terra:low`) also sets
  the level, so the `model` validator refuses a suffix that is one of these levels. Other suffixes stay allowed:
  OpenRouter has IDs such as `anthropic/claude-fable-5.1:batch`.
- **Everything but the model and the system prompt is turned off.** With a planted `AGENTS.md` ("Always end every
  reply with the word BANANA."), a default run replied `OK BANANA` and sent about 2,390 input tokens; a run with the
  flags below replied `OK` and sent 97. The locked run's system message had only two sections: the
  `--system-prompt` text (`SYSTEM_PROMPT` from `generators/base.py`) and a `<cwd>` section with the work directory.
  The flags:
  - `--no-tools`: all built-in, extension, custom and MCP tools.
  - `--no-extensions`: discovered and configured extensions. The user's `pi-subagents` package adds a
    `subagents_enable` tool and system-prompt text otherwise.
  - `--no-skills`, `--no-prompt-templates`, `--no-themes`: skill descriptions in the system prompt, and resources
    the run does not need.
  - `--no-context-files`: `AGENTS.md` and `CLAUDE.md`.
  - `--no-mcp`: no MCP servers start.
  - `--no-approve`: trust-gated project files (`.pi/`) in the work directory or its parents are ignored.
  - `--offline`: no model catalog refresh or version check at startup. A model newer than the cached catalog then
    needs `pi update --models` first (the README says so).
- **No session is kept.** `--no-session` keeps the session in memory. After the test runs, no file under `~/.pi`
  was newer than a stamp file made before them, and the work directory stayed empty. The work directory already
  keeps `prompt.md` and `guide.md`, so there is no `session_dir` option for now.
- **A compacted run fails.** `pi`'s compaction docs (`compaction.md`) say "a provider context-overflow error or an
  early final `stopReason: "length"` can select one compact-and-retry recovery attempt": `pi` replaces part of the
  context with a summary and starts "the retry as a fresh run", while the failed attempt stays in the output. For a
  guide, the part summarised would be the transcript. The retry's assistant message can then pass every other check
  (`stopReason: "stop"`, the same model and thinking level), and the guide would be written from a summary.
  Compaction is on by default, and `compaction.enabled` can only be set in `settings.json`, which `pi-cli` does not
  control. No flag turns it off, and `--no-approve` only ignores a project's `.pi/settings.json`. So `parse_reply`
  fails the run when any `compaction_start` or `compaction_end` event appears, naming the `reason` (`threshold` or
  `overflow`) and suggesting a model with a larger context window. The test runs did not compact: the 161 KB prompt
  used 41,711 of `gpt-6.1-terra`'s 272K tokens.
- **Prompt caching cannot be turned off.** The 161 KB prompt was billed as `cacheWrite: 41708` with `input: 3`.
  `pi`'s providers default to `short` retention; the code has a `none` value, but `PI_CACHE_RETENTION` only maps
  `long`, and no flag or documented setting sets `none`. So, unlike `claude-cli`, there is no `prompt_caching` option,
  and the README says so. No cache-warming refresh happened in the real run (`cacheWarming` defaults to
  `streaming`, a global setting).
- **`pi` exits by itself, so the runner is `subprocess.run`.** The real guide run took 71 seconds and exited 0 right
  after `agent_settled` (`aborted: false`), with no retries. So `run_pi` follows `run_claude`, not `run_agy`'s
  watchdog. It decodes the pipes as UTF-8 with `errors="replace"`, as `run_agy` does: the output is JSON, not locale
  text. The output is large, 2 MB in 5,515 lines for a 6,250-token reply, nearly all `text_delta` events;
  `capture_output` handles that.
- **Lines are split on `\n` only.** `pi`'s JSON docs say the records are split on LF only, and that U+2028 and U+2029
  may appear inside strings. `str.splitlines()` also splits on those (and on `\x1c`-`\x1e`, `\x85`, `\v`, `\f`), so
  `parse_reply` uses `output.split("\n")` and strips a trailing `\r`. (`agy_cli.parse_reply` uses `splitlines()`; it
  is not changed here.)

## The command line

```
pi --mode json --no-session \
   --no-tools --no-extensions --no-skills --no-prompt-templates --no-themes \
   --no-context-files --no-mcp --no-approve --offline \
   --system-prompt <SYSTEM_PROMPT> --model <provider/id> --thinking <effort> [<extra_args>...]
```

The prompt goes on stdin as it is. The process runs in the video's work directory. `--system-prompt` takes "text or
the contents of an existing file"; `SYSTEM_PROMPT` is a sentence, so no file of that name will exist.

Not yet tried:

- A model without thinking support and `effort` other than `off`. The clamp is expected to give `off`, which the
  `thinkingLevel` check turns into an error.
- Automatic retries (`auto_retry_start`): the failed attempt should end in its own assistant `message_end`, so
  `parse_reply` reads the last one.
- A `length` stop reason in JSON mode, and the overflow/length recovery that can follow it: whether the
  `compaction_start` event appears before the retry, as `json.md` describes.
- A model ID with a `:` suffix that is not a thinking level, such as `openrouter/anthropic/claude-fable-5.1:batch`:
  whether `pi` keeps the suffix in the `model` it reports. If it drops it, the model check fails the run, which is
  safe.
- Whether `pi` ever hangs after `agent_settled`, as `agy` 1.5.1 did. One long run is not proof.

The tests in Step 1 use a fake runner, so check these once by hand, along with `pi --help`, whose flags can change
between releases.

## Step 1: `generators/pi_cli.py`

- `GENERATOR_NAME = "pi-cli"`, `HARNESS = "Pi"`.
- `ThinkingLevel = Literal["off", "minimal", "low", "medium", "high", "xhigh", "max"]`.
- `PiCliOptions`, the `[guide.options.pi-cli]` table, with `extra="forbid"` and a `Field(description=...)` on every
  option, so `config options` lists them:
  - `command` (default `["pi"]`).
  - `model`: `str`, default `openai/gpt-6.1-terra`, the model these tests ran. A validator requires `provider/id`
    (a non-empty provider, a `/`, a non-empty ID) and refuses a `:<level>` suffix that is a `ThinkingLevel`.
  - `effort`: `ThinkingLevel`, default `high`, as in `claude-cli`. Always passed as `--thinking`.
  - `timeout_seconds` (default 1800).
  - `extra_args`: refused with `refuse_recorded_flags` when they hold `--model`, `--provider` or `--models`
    ("set the model option instead"), `--thinking` ("set the effort option instead"), or `--mode` ("pi-cli reads
    pi's JSON output"). `pi` has no short forms of these flags.
- `run_pi`, the runner, as `run_claude`: `subprocess.run` with the prompt on stdin, `extra_env` added to the current
  environment, `encoding="utf-8"`, `errors="replace"`. `generate` passes `{}`.
- `PiAssistantMessage` (pydantic, other fields ignored): `provider`, `model`, `thinkingLevel` (optional),
  `stopReason`, `errorMessage` (optional) and `content` (a list of blocks; a text block has `type: "text"` and
  `text`). `PiEvent`: `type` and `message: object`, so other events may have any shape.
- `parse_reply(output, model, effort) -> str`:
  - Splits on `\n`, skips blank lines, and raises `GuideError` for a line that is not a JSON object.
  - A `compaction_start` or `compaction_end` event anywhere raises, with its `reason` (see "A compacted run fails").
  - Keeps the last `message_end` event whose `message.role` is `assistant`; none raises "pi printed no reply."
  - `stopReason` other than `stop` raises, with `errorMessage`; `length` says the reply hit the output limit.
  - `<provider>/<model>` other than the `model` option raises, naming both and pointing to `pi --list-models`.
  - A missing `thinkingLevel` raises "pi did not report the thinking level".
  - `thinkingLevel` other than `effort` raises, naming both and saying the model may not support that level.
  - Joins the `text` blocks; a blank result raises "pi returned an empty reply." Thinking blocks are dropped.
- `PiCliGenerator`:
  - `harness` returns `HARNESS`, `model` returns `options.model`, `effort` returns `options.effort`.
  - `build_args()` builds the command line above.
  - `generate` calls `run_prompt_command` with `display_name="pi"`, the install hint "Is the Pi coding agent
    installed and on PATH?", and `options_table="guide.options.pi-cli"`, then `parse_reply`. `video` is not used.
- Register it in `generators/__init__.py`: its spec in `GUIDE_GENERATORS`, and `GeneratorSpec[PiCliOptions]` in
  `AnyGeneratorSpec`.
- `tests/generators/test_pi_cli.py`, with a fake runner and a helper that builds JSONL output (a `session` header,
  `message_update` events, and a system, user and assistant `message_end`):
  - Harness, model and effort; the command line with defaults, with configured options and with `extra_args`.
  - The prompt reaches the runner unchanged, with `cwd` set to the work directory, and `extra_env` is `{}`.
  - `run_pi`, with a Python script in place of `pi`: the prompt arrives on stdin, `cwd` and `extra_env` reach the
    process, and bytes that are not UTF-8 become U+FFFD.
  - `test_generate_errors`, parametrised as in `test_agy_cli.py`: a missing command, a timeout, a non-zero exit
    status and empty output each become `GuideError`, through `run_prompt_command`.
  - Options validation: accepted models (`openai/gpt-6.1-terra`, `openrouter/openai/gpt-6.1-terra`,
    `openrouter/anthropic/claude-fable-5.1:batch`) and refused ones (`terra`, `/x`, `openai/`, `openai/x:low`); each
    refused `extra_args` form, alone and with `=`.
  - Each `parse_reply` error above, including a model mismatch (`gpt-6.1-terra-pro` for `gpt-6.1-terra`) and a
    thinking mismatch (`low` for `minimal`), a missing `thinkingLevel`, and a `compaction_start` event before an
    assistant `message_end` that would otherwise pass.
  - Two assistant messages (a failed attempt, then a retry): the last one is used.
  - A text block holding U+2028 is returned whole.
  - Thinking blocks are left out; several text blocks are joined.
- `tests/generators/test_registry.py`: the unknown-generator message becomes `Available: agy-cli, claude-cli,
  pi-cli`, and a new `test_make_guide_generator_pi_cli` sits beside `test_make_guide_generator_agy_cli`.
- `tests/cli/test_config_cmd.py`: `test_options_needs_no_config` also asserts `guide.options.pi-cli.model`, and
  `test_show_checks_generator` gets a valid case ("Guides are written by Pi with openai/gpt-6.1-terra (high
  effort).") and an invalid one (`model = "terra"`).

## Step 2: docs

- `docs/design.md`:
  - `pi-cli` in the pipeline diagram, and a `[guide.options.pi-cli]` entry in the configuration example beside
    `[guide.options.agy-cli]`.
  - A "pi-cli generator" section after "agy-cli generator": the command line, the turned-off resources and why, the
    model and thinking checks, why a compacted run fails, and that prompt caching cannot be turned off.
  - Under "Pipeline interfaces", "No tool is asked which model it used: every generator always passes one" is no
    longer true of `pi-cli`. It becomes: every generator always passes a model, and `pi-cli` also checks the one
    that ran.
  - `pi-cli` in the "Generators that run a command" note and in the `harness` list under the front matter.
  - Item 15 of "Build order".
- `README.md`:
  - `pi` in the requirements, and in "How it works" wherever it names only `claude` and `agy`.
  - "Development": the tests mock the `pi` CLI too.
  - A "Writing guides with the Pi coding agent" section: the options table, the `provider/id` rule, that the model
    and thinking level are checked after the run, that a run that compacts the prompt fails, `pi update --models`
    for new models, and that `pi` keeps no session but its prompt caching stays on.
