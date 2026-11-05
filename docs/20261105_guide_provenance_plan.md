# Recording the harness and model in each guide: implementation plan

Written on 2026-11-05. Each guide should show which harness (the tool the generator runs, such as Claude Code),
which model and which effort level wrote it, so guides written with different generators or models can be told
apart. Today nothing in a guide records this: the front matter holds the video's metadata and the `generated` time.
"Guides stay Markdown, with front matter" in [design.md](design.md) already notes that "the generator and model are
not recorded yet: `GuideGenerator` does not report them. Add them if comparing guides across models becomes useful."
With a second generator coming, it does.

The same section rejected a JSON reply from the model, partly because "`GuideGenerator` returns a string". This plan
keeps that: the model still writes Markdown, and `generate` still returns it as a string.

This touches every generator, so it is planned on its own rather than inside the `agy-cli` plan
([20261104_agy_generator_plan.md](20261104_agy_generator_plan.md)). Do it before that plan's Step 1, so `agy-cli` is
written once against the new generator interface. It becomes item 13 of "Build order" in `design.md`, and `agy-cli`
moves to item 14.

## Decisions

Settled on 2026-11-05.

- **Every generator always passes a model.** The `model` option becomes a `str` with a default instead of "unset
  uses the tool's default", and the command line always has `--model`. The recorded model is the one passed, so no
  tool has to report which model it used. That leaves out `claude --output-format json`, whose `modelUsage` names
  the models used, and `agy`, whose stream-json events name no model. An alias such as `opus` is recorded as
  written.
  - `claude-cli` defaults to `claude-opus-5-5`, the model Claude Code 2.1.318 used on this machine in a run with no
    `--model`.
  - `agy-cli` defaults to `gemini-3.8-flash-medium`, the saved default of `agy` 1.4.12 (`agy -p "/config"` shows
    "Gemini 3.8 Flash (Medium)", and `agy models` lists that ID for it).
  - The cost: an always-passed `--model` overrides the tool's own choice. For Claude Code that is `/model`, `model`
    in `settings.json`, `ANTHROPIC_MODEL`, and the model IDs a Bedrock or Vertex setup needs; such a setup must set
    `model`. The default also stays put when the tool's default moves on, until this project changes it.
- **The recorded values must be the ones used.** `extra_args` come last on the command line, so a `--model` or
  `--effort` there would override the option without the front matter knowing. Both generators' options refuse
  `extra_args` that contain `--model` or `--effort`, alone or as `--model=...`.
- **The effort level is recorded too.** For `claude-cli` it is the `effort` option, `null` when unset, which means
  Claude Code's default level. For `agy-cli` it is always `null`: its model IDs name the level
  (`gemini-3.8-flash-medium`), so `agy-cli` has no `effort` option, and the model says it all.
- **The harness is the tool's name:** `Claude Code` for `claude-cli`, `Antigravity CLI` for `agy-cli`. It is a
  constant in each generator's module: no version, and no extra process per guide.
- **It shows in the front matter and on the guide page**, not in the index: `_PAYLOAD_FIELDS` in `static_site.py`
  stays as it is.

## Design

- New front matter keys `harness`, `model` and `effort`, after `generated`, all `str | None`. Guides written before
  this have no way to know them, so they get `null`, which is also how every other unknown value is written.
- `backfill` keeps the three keys when they are set, as it keeps `generated` (`backfill.py`, which rebuilds the
  metadata from the video and copies `generated` over). Without that, each `backfill` run would reset them to
  `null`. `backfill` counts a key missing from the block as changed, so its first run after this rewrites every
  existing guide to add the three keys as `null`, and `--dry-run` lists them for each guide. This happens once.
- `GuideGenerator` gains three read-only properties, `harness: str`, `model: str` and `effort: str | None`, and
  `generate` keeps returning `str`. All three are known before the run, from the generator's module and its options,
  so the pipeline reads them from the generator when it writes the front matter rather than getting them back from
  `generate`.
- The guide page shows a line after the guide's text, such as "Written by Claude Code with claude-opus-5-5 (high
  effort)." It is left out when `harness` is `null`, "with ..." is left out when `model` is `null`, and "(... effort)"
  when `effort` is `null`. The values come from a file a person can edit, so they are HTML-escaped.

## Step 1: the model option

- `generators/claude_cli.py`:
  - `ClaudeCliOptions.model` becomes `str`, with the default `claude-opus-5-5` and `min_length=1`, and a description
    that names the default. Update its docstring entry, which says "None uses Claude Code's default".
  - A validator on `extra_args` that refuses `--model`, `--effort`, `--model=...` and `--effort=...`, and an
    `extra_args` description that says so (e.g. "More arguments for claude, added last; not --model or --effort"),
    so `config options` shows the rule.
  - `build_args` always adds `--model`.
- Tests (`tests/generators/test_claude_cli.py`): the default command line now has `--model claude-opus-5-5`, an
  empty `model` is refused, and each refused `extra_args` form.
- This step changes the command line, so update "claude-cli generator" in `design.md` with it: `--model M` loses
  its brackets, the configuration example's `model` comment gives the default, and a bullet says that `extra_args`
  cannot hold `--model` or `--effort`.

## Step 2: the interface and the front matter

- `generators/base.py`: `harness`, `model` and `effort` properties on `GuideGenerator`, documented in its docstring.
- `generators/claude_cli.py`: `HARNESS = "Claude Code"`; `ClaudeCliGenerator.harness` returns it, and `model` and
  `effort` return the options.
- `front_matter.py`: `harness`, `model` and `effort` on `GuideMetadata`, documented in its docstring. Reword the
  module docstring and the `GuideMetadata` summary, which call the front matter video metadata.
- `pipeline.py`: `write_guide` sets the three keys from the generator with `model_copy(update=...)` on the result of
  `metadata_from_video`, as `backfill` sets `generated`.
- `backfill.py`: keep `harness`, `model` and `effort` when they are set, next to `generated`.
- Tests:
  - The front matter round trip with and without the new keys.
  - `backfill` keeping them, and the exact-value assertions that the new keys change, such as the `changed_keys`
    list and the `describe_changes` text in `tests/test_backfill.py`, and the rendered blocks in
    `tests/test_front_matter.py`.
  - The pipeline writing them.
  - The fake generators (`tests/test_pipeline.py`, `tests/cli/conftest.py`) given the three properties.

## Step 3: the guide page

- `guide_html.py`: `render_guide_page` takes `harness`, `model` and `effort` (default `None`), and fills a new slot
  in `templates/guide_page.html` after `$body`, before `$done_end`, with the line above. It is a small muted line
  with its own class, and it stays in print, unlike `.player-area` and `.done-end`.
- `static_site.py`: pass the three values from `metadata`, as it already passes `metadata.members_only`.
- Tests:
  - `tests/test_guide_html.py`: the line with all three values, without `effort`, with only `harness`, with
    `model` set but `harness` `null` (no line), without any, and with HTML in a value.
  - `tests/test_static_site.py`: the front matter values reach the page.

## Step 4: docs

- `docs/design.md`:
  - "Guides stay Markdown, with front matter":
    - the new keys in the example block;
    - "`write_guide` fills it from `VideoInfo`", since three keys now come from the generator;
    - a bullet in place of "The generator and model are not recorded yet" that says where each value comes from;
    - the `backfill` bullet ("`generated` is kept when set"): the three keys are kept too.
  - "Pipeline interfaces": the `harness`, `model` and `effort` properties of `GuideGenerator`.
  - "Guide pages": the "Written by" line.
  - "Build order": this plan as item 13, marked done, and `agy-cli` renumbered to 14, after items 12 and 13.
- `README.md`:
  - the `model` option's default, and that it overrides Claude Code's own model setting;
  - the sentence "Each guide's front matter records when the video was published and when the guide was written":
    it also records the harness, model and effort.
