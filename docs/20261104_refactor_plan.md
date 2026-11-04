# Module refactor: implementation plan

Written on 2026-11-04. The code is reorganised so each module has one job, and adding a guide generator touches one
package. It prepares for a second generator, Antigravity CLI (`agy`), which has its own plan:
[20261104_agy_generator_plan.md](20261104_agy_generator_plan.md). This is item 12 of "Build order" in
[design.md](design.md). Each step passes the tests and changes no behaviour: the steps move or extract code, so a
green test suite after each one shows nothing changed.

## Open decisions

- **Typing the registry.** `dict[str, GeneratorSpec[Any]]` may trip basedpyright's explicit `Any` rule. If it does,
  annotate the dict with a union of the concrete spec types.

## Why

- `pipeline.py` holds five concerns: the transcript provider registry, the work directory cache (`video.json`,
  `transcript.json`), the generator registry, the rules for finding and replacing existing guides (`GuideScan`,
  `guides_to_replace`, `check_existing_guides`), and `write_guide`.
- `cli.py` (1368 lines) holds pipeline steps: `_GuideTools`, `_PreparedGuide`, `_make_guide_tools`, `_prepare_guide`,
  `_generate_guide` and `_make_ytdlp`. They live there only because they show `STDERR.status(...)` spinners and print
  warnings.
- A new generator needs edits in three places: its factory in `pipeline.py`, its options model in `config options`
  (`cli.py`, which hardcodes `ClaudeCliOptions` and `GENERATOR_NAME`), and `docs/design.md`, which says a new
  generator's options "must be added to the command by hand". The registry maps a name to a bare factory, so it cannot
  tell `config options` which options model each generator has.
- `claude_cli.py` mixes generic work (running a command with the prompt on stdin, and turning a missing command,
  timeout, non-zero exit or empty reply into `GuideError`) with the `claude` flags. A second generator that runs a
  command, such as `agy`, would copy the generic part.
- `cli.py` puts every command in one file. `browse` alone is about 400 lines, and `tests/test_cli.py` is 1021.

## Target layout

```
src/ces_youtube_tools/
  cache.py             CacheError, work_dir_for, write_cache_text, load_video_info, load_transcript,
                       private model cache I/O (_load_cached, _save)
  existing_guides.py   GuideScan, find_guides_for_video, is_same_file, guides_to_replace, check_existing_guides
  guide.py             prompt rendering, reply cleanup, link check, file name (minus the Protocol)
  generators/
    base.py            GuideGenerator (Protocol), GeneratorSpec
    __init__.py        GUIDE_GENERATORS registry, make_guide_generator
    _subprocess.py     run_prompt_command: stdin, timeout, exit status, empty reply -> GuideError
    claude_cli.py      ClaudeCliOptions, ClaudeCliGenerator (moved, thinner)
  pipeline.py          composition and orchestration only: make_ytdlp, make_transcript_provider, GuideTools,
                       make_guide_tools, PreparedGuide, prepare_guide, generate_guide, write_guide,
                       load_prompt_template, GUIDE_ERRORS
  cli/
    __init__.py        `from .app import APP` (the `ces_youtube_tools.cli:APP` entry point is unchanged)
    __main__.py        APP()
    app.py             APP, CONFIG_APP, main() callback, help_text, command(); registers every command
    common.py          CONSOLE, STDERR, ConfigPathOption, print_error, fail, load_config_or_exit
    guide_report.py    report_guide, shared by `guide` and `browse`
    menu.py            moved from the top level; only the CLI uses it
    pickers.py         moved from the top level; only the CLI uses it
    video_menu.py      the paged video multi-select over a VideoPager
    config_cmd.py      the `config` subcommands, with _pick_profile, _choose, _write_default_template
    list_cmd.py        `list`, with _videos_table
    transcript_cmd.py  `transcript`
    guide_cmd.py       `guide`, with _run_guide
    backfill_cmd.py    `backfill`
    site_cmd.py        `site`
    browse_cmd.py      `browse`, with _plan_guides, _write_guides, _confirm_replace, _outcomes_table
```

`firefox.py` and `formatting.py` stay at the top level: `pipeline.make_ytdlp` uses `firefox.py`, and `guide.py` uses
`formatting.py`.

Each module then answers one question: where results are cached (`cache.py`), what is already on disk
(`existing_guides.py`), what goes to and comes back from the model (`guide.py`), who calls the model (`generators/`),
in what order the steps run (`pipeline.py`), and what the user sees (`cli/`).

## Design decisions

### A spec per generator

```python
# generators/base.py
@dataclass(frozen=True)
class GeneratorSpec[OptionsT: BaseModel]:
    name: str
    options_model: type[OptionsT]
    build: Callable[[OptionsT], GuideGenerator]

    def create(self, raw: dict[str, Any]) -> GuideGenerator:
        return self.build(self.options_model.model_validate(raw))


# generators/__init__.py
GUIDE_GENERATORS = {
    spec.name: spec
    for spec in (
        GeneratorSpec(claude_cli.GENERATOR_NAME, ClaudeCliOptions, ClaudeCliGenerator),
        # A new generator adds its spec here.
    )
}
```

- The registry keeps today's name, `GUIDE_GENERATORS`, which pairs with `TRANSCRIPT_PROVIDERS`.
- `make_guide_generator(config)` looks up the spec and calls `spec.create(config.guide.options.get(name, {}))`. It
  turns a `ValidationError` into the existing `ValueError` naming `[guide.options.<name>]`.
- `config options` loops over `GUIDE_GENERATORS` and adds
  `describe_options(spec.options_model, f"guide.options.{name}.")` for each one, so a new generator shows up there
  without a CLI change.
- The specs are built in `generators/__init__.py`, not in each generator module. Generator modules never import the
  registry, so the imports only go one way.
- `create` keeps the options type inside the spec. The registry can then hold specs with different options models,
  and `make_guide_generator` never needs to know them.

### The status spinner is passed in

```python
StatusFactory = Callable[[str], AbstractContextManager[object]]


def prepare_guide(
    tools: GuideTools, video_id: str, target_dir: Path, *, refresh: bool, status: StatusFactory = _no_status
) -> PreparedGuide: ...


def generate_guide(
    tools: GuideTools,
    prepared: PreparedGuide,
    *,
    force: bool,
    refresh: bool,
    status: StatusFactory = _no_status,
) -> GuideResult: ...
```

- Both functions show a spinner today: "Fetching video metadata..." in `prepare_guide`, then "Fetching transcript..."
  and "Writing the guide with ..." in `generate_guide`. The CLI passes `status=STDERR.status`, and tests pass nothing.
  `_no_status` returns `contextlib.nullcontext()`.
- The other prints become data that the CLI prints. `GuideTools.template_is_default` covers the built-in template
  notice, and `PreparedGuide.scan.warnings` already holds the scan warnings.
- `GuideTools` also holds `cache_dir` and `generator_name`, so `prepare_guide` and `generate_guide` take no
  `AppConfig`.
- `GUIDE_ERRORS` (today's `_GUIDE_ERRORS`) moves to `pipeline.py`, next to the functions whose `Raises:` sections it
  must match.

### Commands are registered in one place

Command modules define plain functions without decorators. `cli/app.py` imports them and registers each one:

```python
_ = command(CONFIG_APP, "init")(config_cmd.config_init)
...
_ = command(APP, "list")(list_cmd.list_videos)
_ = command(APP, "browse")(browse_cmd.browse)
```

- Command modules import `common.py`, never `app.py`, so there is no import cycle.
- `--help` lists the commands in today's order, set by one file, not by import order.
- `app.py` reads as the command tree.

### Module names end in `_cmd`

Plain names would repeat top-level module names (`cli/config.py` beside `config.py`, also `guide`, `transcript`,
`backfill`), which makes tracebacks, grep results and relative imports ambiguous. `cli/list.py` would also shadow the
`list` builtin wherever it is imported.

### `browse` is split in two

`video_menu.py` is the paged multi-select, a widget over `VideoPager` with its own state: the selection is kept by
video ID across pages, and previous and next page entries are added to the menu. `browse_cmd.py` keeps the guide
work: plan, ask about replacing guides, write, and summarise.

## Tests

- Tests mirror the package (see "Testing Strategy" in `AGENTS.md`): `tests/test_cache.py`,
  `tests/test_existing_guides.py`, `tests/generators/test_registry.py` and `tests/generators/test_claude_cli.py`.
  `tests/test_cli.py` becomes `tests/cli/`, with one file per command module, plus `test_app.py` (`test_help_runs`,
  `test_help_text_stops_at_docstring_sections`), `test_video_menu.py`, `test_menu.py` and `test_pickers.py`. The tests
  in `tests/test_backfill.py` that invoke `APP` move to `tests/cli/test_backfill_cmd.py`, and the tests of
  `backfill.py` stay. Shared fixtures (`config_path`, `guide_setup`, `FakeGuideGenerator`) go in
  `tests/cli/conftest.py`. Each new test folder gets an `__init__.py`, as `tests/` has.
- A moved name must be patched in the module that looks it up. `monkeypatch.setattr(cli, "pick_many", ...)` changes
  the package attribute and nothing else, so `video_menu` would still call the real `pick_many` and the patch would
  silently do nothing. The same function can need different targets: `transcript` and `backfill` call
  `load_video_info` from the CLI even after Step 3, while `guide` and `browse` call it through `pipeline`. The targets:

| Patched name                         | Patched in                                | Patch it in                      |
| ------------------------------------ | ----------------------------------------- | -------------------------------- |
| `resolve_channel`, `verify_login`    | `config` tests                            | `cli.config_cmd` (Step 4)        |
| `list_latest_videos`                 | `list` tests                              | `cli.list_cmd` (Step 4)          |
| `load_video_info`, `load_transcript` | `test_transcript` (`test_cli.py:208-209`) | `cli`, then `cli.transcript_cmd` |
| `load_video_info`                    | backfill fixture (`test_backfill.py:275`) | `cli`, then `cli.backfill_cmd`   |
| `rename_guide`                       | `test_backfill.py:334`                    | `cli.backfill_cmd` (Step 4)      |
| `load_video_info`, `load_transcript` | `guide_setup`, `titled`, tests over them  | `pipeline` (Step 3)              |
| `make_guide_generator`               | `guide_setup`                             | `pipeline` (Step 3)              |
| `VideoPager`, `pick_one`             | `browse` tests                            | `cli.browse_cmd` (Step 4)        |
| `require_terminal`                   | `browse` tests                            | `cli.browse_cmd` (Step 4)        |
| `pick_many`                          | `browse` tests                            | `cli.video_menu` (Step 4)        |

"`cli`, then ..." means the patch stays on `cli` until that command's module is extracted in Step 4.

- Tests also read attributes of `cli`, and these must move with the names:
  - `test_cli.py:802` reads `cli.load_transcript` and `:832` reads `cli.load_video_info`, to wrap the fixture's fake.
    They move to `pipeline` in Step 3, with the patches. Left on `cli`, they would wrap the real function, which
    `cli` still imports for `transcript` and `backfill`.
  - `test_cli.py:605` reads `cli._NEXT_PAGE_LABEL`. It moves to `cli.video_menu` when that module is extracted.
  - `test_cli.py:365` reads `cli.help_text`. It moves to `cli.app` in Step 4.4, when `cli/__init__.py` stops
    exporting anything but `APP`.

## Step 1: `cache.py` and `existing_guides.py`

- Move the cache functions and `CacheError` from `pipeline.py` to `cache.py`, and the guide scan and replacement
  rules to `existing_guides.py`. Move the module docstring's work directory layout to `cache.py`.
- `_write_cache_text` becomes the public `write_cache_text`, since `write_guide` stays in `pipeline.py` and writes
  `prompt.md` and `guide.md` with it. `_load_cached` and `_save` stay private to `cache.py`.
- Update the imports in `cli.py`, `backfill.py` (it imports `CacheError` and `is_same_file` from `pipeline`) and the
  tests. Split the matching tests out of `tests/test_pipeline.py`.

## Step 2: the `generators/` package

- Add `generators/base.py` with `GuideGenerator`, moved from `guide.py`, and `GeneratorSpec`. Add `GUIDE_GENERATORS`
  and `make_guide_generator` in `generators/__init__.py`. Move `claude_cli.py` to `generators/claude_cli.py`.
- Make `config options` loop over `GUIDE_GENERATORS`.
- Imports to update:
  - `cli.py` imports `GuideGenerator` from `.guide` and `make_guide_generator` from `.pipeline`. They now come from
    `.generators.base` and `.generators`. Its import of `GENERATOR_NAME` and `ClaudeCliOptions` from `.claude_cli`
    goes away.
  - `pipeline.py` imports `GuideGenerator` from `.generators.base`, for `write_guide`'s annotation. Its `.claude_cli`
    import goes away. It does not import `make_guide_generator` yet: nothing in it calls that until Step 3, and an
    import kept only for `cli.py` would trip ruff's F401.
  - `tests/test_config.py` imports `ClaudeCliOptions` and `GENERATOR_NAME` from the new path.
- Tests to move: `tests/test_claude_cli.py` to `tests/generators/test_claude_cli.py`, and the `make_guide_generator`
  tests (with their `ClaudeCliGenerator` import) from `tests/test_pipeline.py` to `tests/generators/test_registry.py`.
  `test_options_needs_no_config` (`tests/test_cli.py`) must still find the `claude-cli` rows.
- `docs/design.md`: remove the sentence saying a new generator's options must be added to `config options` by hand,
  and describe `GeneratorSpec` under "Pipeline interfaces".

## Step 3: guide orchestration into `pipeline.py`

- Move `_make_ytdlp`, `_GuideTools`, `_PreparedGuide`, `_make_guide_tools`, `_prepare_guide`, `_generate_guide` and
  `_GUIDE_ERRORS` from `cli.py` to `pipeline.py` as public names, with the `status` argument and the data fields
  above.
- `pipeline.py` now imports `make_guide_generator` from `.generators`, for `make_guide_tools`, and `cli.py` stops
  importing it.
- `cli.py` keeps `_report_guide`, `_plan_guides`, the menus and the outcome table, and prints the template notice and
  the scan warnings.
- Point the patches in the `guide_setup` and `titled` fixtures, the tests that patch over them, and the reads at
  `test_cli.py:802,832` at `pipeline`. Leave the `transcript` and `backfill` patches on `cli` (see "Tests").
- Add `tests/test_pipeline.py` tests for `prepare_guide` and `generate_guide` with fakes and no `status`.

## Step 4: the `cli/` package

Done as several commits, each a move with only the import edits it forces:

1. `git mv src/ces_youtube_tools/cli.py src/ces_youtube_tools/cli/__init__.py`, and add `__main__.py` with the
   `if __name__ == "__main__"` block. Every relative import in the moved file now resolves inside
   `ces_youtube_tools.cli`, so each `from .x import` becomes `from ..x import`. All `cli.X` patches still work.
   `git log --follow` keeps the history.
2. Extract `common.py`. Move `menu.py` and `pickers.py` into `cli/`, with their tests. The CLI's imports of them go
   back to `from .menu` and `from .pickers`. Neither module has relative imports of its own.
3. Extract one command module at a time, simplest first: `site_cmd`, `list_cmd`, `transcript_cmd`, `backfill_cmd`,
   `config_cmd`, then `guide_cmd` with `guide_report`, then `video_menu` and `browse_cmd`. Each module imports
   top-level modules with `from ..x` and shared CLI code with `from .common`. Until `app.py` exists, `cli/__init__.py`
   registers each extracted command at its current place, so `--help` keeps today's order after every commit. Move
   each module's tests, patch targets and attribute reads in the same commit.
4. Add `app.py` with the central registration. `__init__.py` becomes the one-line re-export. Move the
   `cli.help_text` read to `cli.app`. Check that `--help` lists the commands in today's order.
5. `docs/design.md`: "CLI conventions" says to register commands with `@command(APP, "name")` from `cli.py`. Change
   it to the central registration in `cli/app.py`, and name the `cli/` package instead of `cli.py` there and elsewhere
   in the file.

Step 4 comes after Step 3 so the orchestration code is moved once, to `pipeline.py`, and never first into
`guide_cmd.py` or `browse_cmd.py`.

## Step 5: `generators/_subprocess.py`

- Extract `run_prompt_command` from `ClaudeCliGenerator.generate`:

  ```python
  def run_prompt_command(
      runner: PromptRunner,
      args: list[str],
      prompt: str,
      cwd: Path,
      timeout: float,
      extra_env: dict[str, str],
      *,
      display_name: str,
      install_hint: str,
      options_table: str,
  ) -> str: ...
  ```

  It calls the runner and turns `FileNotFoundError`, `TimeoutExpired`, a non-zero exit status and an empty reply into
  `GuideError`.
- The runner is passed in, as today. `run_claude` stays in `claude_cli.py`, since it sets
  `CLAUDE_CODE_DISABLE_CLAUDE_MDS`, and tests keep injecting a fake.
- The `claude` messages stay exactly as they are. `ClaudeCliGenerator` passes `display_name="claude"`,
  `install_hint="Is Claude Code installed and on PATH?"` and `options_table="guide.options.claude-cli"`, which give
  "Cannot run '<command>'. Is Claude Code installed and on PATH?", "claude did not finish within ... Raise
  timeout_seconds in [guide.options.claude-cli].", "claude exited with status ..." and "claude returned an empty
  reply.". `tests/generators/test_claude_cli.py` must pass without changes.
- The missing-command message names the command that was run, `args[0]!r`, as today, so `command = ["npx",
  "claude"]` still gives "Cannot run 'npx'. ...". `display_name` appears only in the other three messages.
- `docs/design.md`: mark item 12 of "Build order" done.
