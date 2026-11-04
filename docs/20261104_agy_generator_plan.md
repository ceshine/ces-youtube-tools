# The `agy` generator: implementation plan

Written on 2026-11-04. A second guide generator, `agy-cli`, writes guides with Antigravity CLI (`agy`) in print
mode, as `claude-cli` does with Claude Code. This is item 13 of "Build order" in [design.md](design.md). It builds
on the module refactor ([20261104_refactor_plan.md](20261104_refactor_plan.md), item 12): Step 2 there adds the
`GeneratorSpec` registry this generator registers in, and Step 5 adds the shared `run_prompt_command` it runs `agy`
with. Start once both are done, and after the open decisions below are settled.

## Open decisions

- **How `agy -p` takes the prompt.** The help does not say whether `--print` reads stdin. If the prompt must be a
  command-line argument, Linux limits one argument to 128 KiB (`MAX_ARG_STRLEN`), which a transcript of a long
  livestream can exceed. Try `echo "say hi" | agy -p` first. If stdin is not read, look for another input before
  passing the prompt as an argument. `--input-format stream-json` reads NDJSON from stdin, but it requires
  `--output-format stream-json`, so the reply would then have to be parsed out of NDJSON output.
- **Keeping `agy` from using tools.** Without `--tools ""`, the agent runs in the video's work directory and could
  read or change files there. Choose between `--mode plan`, `--sandbox`, both, or a `--settings` JSON that turns tools
  off, after checking what each one allows. Also check what happens when the agent asks for a tool permission in
  `--print` mode: whether the request is denied or the run waits until `--print-timeout`. The help lists
  `--dangerously-skip-permissions`, so such requests can happen. That flag is not an option here.
- **The system prompt.** With no `--system-prompt`, `claude_cli.SYSTEM_PROMPT` has to be put before the prompt text,
  unless `--settings` or `--agent` offers something better.
- **Session logs.** Find out where `agy` keeps its conversations and whether they can be turned off, to match
  `claude-cli`'s default of keeping none. Check `--project`, `--new-project` and `--log-file`.

## What `agy` offers

`agy --help`, checked on 2026-11-04. Among its flags:

- It has `--print` (alias `-p`), `--model`, `--effort` (`low`, `medium`, `high`, `xhigh`, `max`),
  `--output-format text`, `--disable-slash-commands`, `--print-timeout`, `--mode` (`accept-edits`, `auto`, `plan`),
  `--sandbox` and `--settings`.
- It has no equivalent of `claude`'s `--tools ""`, `--system-prompt` or `--no-session-persistence`, which
  `ClaudeCliGenerator` relies on. The open decisions above cover each one.

Check the help again before Step 1: a CLI's flags can change between releases.

## Step 1: `generators/agy_cli.py`

- `GENERATOR_NAME = "agy-cli"`.
- `AgyCliOptions`, the `[guide.options.agy-cli]` table, with `extra="forbid"` and a `Field(description=...)` on every
  option, so `config options` lists them. Likely options, after `ClaudeCliOptions`: `command` (default `["agy"]`),
  `model`, `effort`, `timeout_seconds` and `extra_args`, plus whatever the open decisions settle on.
- A runner for `agy`, as `run_claude` is for `claude`, setting any environment the open decisions call for.
- `AgyCliGenerator`: builds the command line and calls `run_prompt_command` with `display_name="agy"`, an install hint
  and `options_table="guide.options.agy-cli"`.
- Register its spec in `GUIDE_GENERATORS` (`generators/__init__.py`).
- `tests/generators/test_agy_cli.py`: the built command line, options validation, and each error path, with a fake
  runner. `test_options_needs_no_config` (in `tests/cli/` after refactor Step 4) should find the `agy-cli` rows too.

## Step 2: docs

- `docs/design.md`: an "agy-cli generator" section next to "claude-cli generator", with the command line it runs, and
  item 13 of "Build order" marked done.
- `README.md`: how to choose the generator (`guide.generator = "agy-cli"`) and its options.
