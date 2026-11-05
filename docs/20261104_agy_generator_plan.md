# The `agy` generator: implementation plan

Written on 2026-11-04. A second guide generator, `agy-cli`, writes guides with Antigravity CLI (`agy`) in print
mode, as `claude-cli` does with Claude Code. This is item 14 of "Build order" in [design.md](design.md). It builds
on the module refactor ([20261104_refactor_plan.md](20261104_refactor_plan.md), item 12, done): Step 2 there added
the `GeneratorSpec` registry this generator registers in, and Step 5 added the shared `run_prompt_command` it runs
`agy` with. Do [20261105_guide_provenance_plan.md](20261105_guide_provenance_plan.md) first: it adds the `harness`,
`model` and `effort` properties to `GuideGenerator`, and makes every generator pass a model.

## Decisions

Settled on 2026-11-04 by running `agy` 1.4.12. The test runs are described under each decision.

- **The prompt goes to stdin as stream-json.** In text mode `agy` never reads stdin: `-p` needs a value, an empty
  one is refused ("empty prompt"), and `-p -` sends "-" as the prompt. A prompt passed as an argument fails at
  `exec` once it is over 128 KiB (`MAX_ARG_STRLEN`): a 141 KiB prompt failed with "Argument list too long". With
  `--input-format stream-json --output-format stream-json` and no `-p`, `agy` reads one JSON message per line from
  stdin, and the same 141 KiB prompt worked. The message is `{"event":"user","message":{"content":"<prompt>"}}`. The
  help does not give this shape; it comes from the error messages for wrong shapes. This is still print mode (the
  changelog adds `--input-format stream-json` "to print mode"): every stream-json run answered the one message and
  exited by itself once stdin closed.
- **The reply is read from the `result` event.** The output is NDJSON: an `init` event, `step_update` events, and a
  final `result` event, e.g.
  `{"event":"result","result":{"status":"SUCCESS","response":"<reply>",...}}`. On failure, `status` is `ERROR` and
  `error` says why. A turn that ends early (`--print-timeout` expired, interrupted, failed) has a `finished_reason`
  field, which is absent when the turn finishes normally. The exit status does not show every failure: a run whose
  turn was ended by a denied tool exited 0 with `status: SUCCESS` and an empty `response`.
- **Tools are turned off with a custom agent.** Without one, the agent read and wrote files in its working directory
  in the default mode, with `--mode plan` and with `--sandbox` alike; only shell commands were refused. These runs
  used stream-json input as above. A tool that needs a permission is refused at once (listed under `denied_actions`
  in the result), not left waiting. `--settings` deny rules (`command(*)`, `read_file(*)`, `write_file(*)`,
  `read_url(*)`, `execute_url(*)`, `mcp(*)`) blocked those tools, and won over the user's own allow rules, but not
  `search_web`. A Markdown custom agent with `tools: []` and `excludeDefaultComponents: true` made no tool calls at
  all, and a short prompt took 445 input tokens instead of about 7,400. The `init` event lists every tool even then,
  so it does not show what the agent can use.
- **The system prompt is the agent's body.** `SYSTEM_PROMPT` goes after the agent file's `# ` heading.
  The custom agent also leaves out the user's rules: an `AGENTS.md` in the working directory applied to the default
  agent and not to the custom one.
- **The agent file lives in a temporary directory.** `--agent` takes a name, not a path ("unknown agent" for a
  path). `agy` finds agents in `.agents/agents/` of the working directory and its parents, and of directories given
  with `--add-dir`. The generator writes the agent into a `tempfile.TemporaryDirectory()` for each run and passes it
  with `--add-dir`, so the video's work directory gets no `.agents/` folder and the file always matches
  `SYSTEM_PROMPT`. The agent has no tools, so adding that directory to the workspace exposes nothing.
- **The model passed is the model used.** Checked on 2026-11-05 with the custom agent below: with `--model
  claude-sonnet-4-6`, `agy`'s log (`~/.gemini/antigravity-cli/log/`) has `Propagating selected model override to
  backend: label="Claude Sonnet 4.6 (Thinking)"`, and the reply took 919 input tokens instead of 445. An unknown ID
  (`--model not-a-model`) is refused before any turn: exit 1, `status: ERROR` with "invalid model selection" and the
  available models in `error`, and `finished_reason: "error"`. So the `model` recorded in the front matter is the one
  that wrote the guide. The model IDs name an effort level (`gemini-3.8-flash-medium`), and the error message shows
  `--effort` as part of the model selection, so `agy-cli` has no `effort` option: the model ID says it all.
- **Only Gemini model IDs that end in their effort level.** Settled on 2026-11-05 with `agy` 1.5.1, which has a
  separate `--effort` flag and lists IDs both with and without a level (`gemini-3.8-flash-medium`,
  `claude-sonnet-4-6`). Runs with the custom agent below and `--effort` added showed:
  - An ID that names a level refuses another level: `--model gemini-3.8-flash-medium --effort high` exits 1 with
    `status: ERROR` and "conflicts with --effort=high", before any turn. `--effort medium` with it is accepted.
  - Claude models refuse `--effort` outright: "--effort is not supported for model "claude-sonnet-4-6"".
  - A family name works without a level, and picks one the front matter would not record: `gemini-3.8-flash`
    alone ran as "Gemini 3.8 Flash (High)" in the log, not the saved default (Medium). On the same prompt it took
    824 thinking tokens, against 458 for `gemini-3.8-flash-medium`; `gemini-3.8-flash --effort high` ran as High too.

  So `AgyCliOptions.model` accepts only `gemini-...-<level>`, with the levels `agy --help` lists for `--effort`
  (`low`, `medium`, `high`, `xhigh`, `max`), and the recorded model always names the level; `effort` stays `null`.
  A family name, a Claude model or any other non-Gemini model, such as `gpt-oss-120b-medium`, fails when the
  configuration is loaded.
- **Session logs cannot be turned off.** Every run, stream-json included, stores the conversation with the full
  prompt in `~/.gemini/antigravity-cli/conversations/<conversation_id>.db`, and adds a `brain/<conversation_id>/`
  folder, entries in `annotations/` and `implicit/`, and a log under `log/`. No flag or setting stops this:
  `--log-file` only moves the log, and `--project`/`--new-project` only choose the project the conversation belongs
  to. Deleting those files after each run would depend on `agy`'s private storage layout, so the generator leaves
  them, and the README says so. Unlike `claude-cli`, there is no `session_dir` option.

## The command line

```
agy --add-dir <temporary directory> --agent ces-guide-writer \
    --input-format stream-json --output-format stream-json --disable-slash-commands \
    --model <model> [<extra_args>...]
```

The prompt is one stream-json line on stdin: `json.dumps({"event": "user", "message": {"content": prompt}}) + "\n"`.
`json.dumps` escapes the newlines in the prompt, so the message stays on one line.
`<temporary directory>/.agents/agents/ces-guide-writer.md` is:

```markdown
---
name: ces-guide-writer
description: Writes watching guides from video transcripts.
mainAgent: true
tools: []
excludeDefaultComponents: true
---
# Guide writer

<SYSTEM_PROMPT>
```

`mainAgent: true` lets `--agent` select the agent as the main agent of the run, as opposed to a subagent. The
process runs in the video's work directory. `--print-timeout` is not passed: `agy --help` gives its default as 0s,
which "waits until the turn completes", and the changelog says the default changed from 5 minutes to unlimited. So
`timeout_seconds`, enforced by the subprocess timeout as for `claude-cli`, is the only time limit.

Not yet tried:

- `--disable-slash-commands` with stream-json input.
- Whether `mainAgent: true` is required; the runs above always set it.
- Which agent wins when another `ces-guide-writer` is found too, e.g. in a parent of the work directory.

The tests in Step 1 use a fake runner, so check these once by hand, along with `agy --help`, whose flags can change
between releases.

## Step 1: `generators/agy_cli.py`

- `GENERATOR_NAME = "agy-cli"`.
- Move `SYSTEM_PROMPT` from `claude_cli.py` to `generators/base.py`, next to the `GuideGenerator` interface, and
  import it from there in both generators, so neither generator module imports the other.
- `AgyCliOptions`, the `[guide.options.agy-cli]` table, with `extra="forbid"` and a `Field(description=...)` on every
  option, so `config options` lists them. Modelled on `ClaudeCliOptions`: `command` (default `["agy"]`), `model`,
  `timeout_seconds` and `extra_args`, and no `effort` (see "The model passed is the model used"). `model` is a `str`
  defaulting to `gemini-3.8-flash-medium`, the saved default of `agy` 1.4.12, and is always passed. A validator
  accepts only Gemini model IDs that end in their effort level (see "Only Gemini model IDs that end in their effort
  level"). As in `ClaudeCliOptions`, `extra_args` that contain `--model` or `--effort`, alone or as `--model=...`,
  are refused, so the front matter records what ran, and the `extra_args` description says so.
- `AGENT_NAME = "ces-guide-writer"` and a function that returns the agent file's text from `SYSTEM_PROMPT`.
- `run_agy`, the runner, as `run_claude` is for `claude`: `subprocess.run` with the prompt on stdin and `extra_env`
  added to the current environment. `agy` needs no variable of its own, so `generate` passes `{}`.
- `AgyCliGenerator`:
  - `harness` returns `HARNESS = "Antigravity CLI"`, `model` returns `options.model`, and `effort` returns `None`.
  - Builds the command line above.
  - In `generate`, writes the agent file into a temporary directory, encodes the prompt as the stream-json line, and
    calls `run_prompt_command` with `display_name="agy"`, an install hint and `options_table="guide.options.agy-cli"`.
    That turns a missing command, a timeout, a non-zero exit status and empty output into `GuideError`. A
    `status: ERROR` result came with exit 1 and an `error: ...` line on stderr, and the changelog puts the
    `AGY_ERROR: {...}` line of exit 3 on stderr too, so `run_prompt_command`'s message, which prefers stderr,
    includes `agy`'s reason.
  - Parses the output: takes the last line whose `event` is `result`, and returns `result.response`. Raises
    `GuideError` when no line is a `result` event, when a line is not JSON, when `status` is not `SUCCESS` (with
    `error` in the message), when `finished_reason` is set, or when `response` is blank. The `status` check covers a
    `status: ERROR` with exit 0, which the runs above did not show.
- Register it in `generators/__init__.py`: its spec in `GUIDE_GENERATORS`, and its spec type in `AnyGeneratorSpec`
  (`GeneratorSpec[ClaudeCliOptions] | GeneratorSpec[AgyCliOptions]`).
- `tests/generators/test_agy_cli.py`, with a fake runner:
  - The built command line, with the default and a configured `model`, and with and without `extra_args`.
  - The stdin line, and the agent file the run writes, read by the fake runner while the temporary directory exists.
  - Options validation, including refused models (empty, a family name, Claude) and each refused `extra_args`
    form.
  - Each parsing error path above.
  - Output with `init` and `step_update` events before the `result` event, of which only `result.response` is
    returned.
- `tests/generators/test_registry.py`: the unknown-generator message becomes `Available: agy-cli, claude-cli`, so
  update its `match`, and add an `agy-cli` case to `test_make_guide_generator`.
- `test_options_needs_no_config` (`tests/cli/test_config_cmd.py`): also assert that `guide.options.agy-cli.model` is
  in the output.

## Step 2: docs

- `docs/design.md`: an "agy-cli generator" section next to "claude-cli generator", with the command line, the agent
  file and why it is used, that `extra_args` cannot hold `--model` or `--effort`, and item 14 of "Build order"
  marked done.
- `README.md`: how to choose the generator (`guide.generator = "agy-cli"`), its options, and that `agy` keeps every
  conversation, prompt included, under `~/.gemini/antigravity-cli/`.
