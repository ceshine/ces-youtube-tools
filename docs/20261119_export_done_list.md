# Export & Import Done List: Implementation Plan

Written on 2026-11-19. Adds **Export** and **Import** buttons to the index page so a reader can back up their "Done"
marks from `localStorage` to a JSON file and restore or transfer them to another browser or device.

Everything stays client-side with zero third-party dependencies, following the existing pattern: pure functions in
`site_done.mjs` (tested with Node's `node:test`), DOM/file wiring in `site_index.mjs`, and minimal HTML/CSS in
`site_index.html`.

## File Format

A versioned, pretty-printed JSON object with sorted video IDs:

```json
{
  "format": "ces-youtube-tools:done",
  "version": 1,
  "done": [
    "dQw4w9WgXcQ",
    "oHg5SJYRHA0"
  ]
}
```

- **Filename on export:** `ces-youtube-tools-done-YYYY-MM-DD.json` (using the browser's local date).
- **Validation on import:** Must be valid JSON, a plain object with `format === "ces-youtube-tools:done"`,
  `version === 1`, and `done` as an array of non-empty strings. Duplicate IDs are deduplicated via `Set`.

## Decisions

Decided on 2026-11-19:

1. **Import behavior — Merge:** Union imported IDs with the current browser's marks (non-destructive).
2. **Unknown video IDs:** Keep imported IDs even if they are not in the current index's guide list, so rebuilding
   the site or importing across subsets does not silently drop marks.

## Step 1: Pure Export/Import Functions in `site_done.mjs` & Node Tests

- **`src/ces_youtube_tools/templates/site_done.mjs`:**
  - Export constants `EXPORT_FORMAT = "ces-youtube-tools:done"` and `EXPORT_VERSION = 1`.
  - `formatDoneExport(done) -> string`: returns pretty-printed JSON (2-space indent + trailing newline) with sorted
    `done` IDs.
  - `parseDoneExport(text) -> Set<string>`: parses and validates the export schema; throws a descriptive `Error` for
    invalid JSON, wrong `format`, unsupported `version`, or non-string / empty entries in `done`.
  - `mergeDone(storage, imported) -> { ok: boolean, added: number, total: number }`: reads the latest marks from
    `storage` (preserving marks written in other tabs), unions them with `imported`, short-circuits without writing
    when no new IDs are added, otherwise writes back with `formatDone`, and returns `{ ok, added, total }`. Returns
    `{ ok: false, added: 0, total: 0 }` with a `console.warn` if storage cannot be read or written.
- **`tests/js/site_done.test.mjs`:**
  - Round-trip `formatDoneExport` -> `parseDoneExport`, sorting, and empty set.
  - Rejection tests for malformed JSON, bare arrays, wrong `format`, unsupported `version`, missing `done`, and
    non-string or empty-string IDs.
  - `mergeDone` tests for merging new and existing IDs, deduplication, empty import leaving storage untouched,
    cross-tab freshness, replacing an unreadable stored value, and broken/full storage handling.

## Step 2: Index Page UI & Wiring

- **`src/ces_youtube_tools/templates/site_index.html`:**
  - Add a `#done-actions` group inside `#controls` (starting `hidden`, shown only when `localStorage` is available,
    matching `#show-row`):
    - `<button type="button" id="export-done">Export done</button>`
    - `<button type="button" id="import-done">Import done</button>`
    - `<input type="file" id="import-done-file" accept=".json,application/json" hidden>`
  - Add a status line (e.g. `<p class="done-status" id="done-status" role="status" aria-live="polite" hidden></p>`)
    near the controls/count line to show import/export results or validation errors without `alert()`.
  - Add minimal CSS matching the existing `.controls` / `.count` styling (including `[hidden] { display: none }` for
    the flex container and a clear error color for invalid imports).
- **`src/ces_youtube_tools/templates/site_index.mjs`:**
  - Toggle `#done-actions` visibility alongside `#show-row` whenever `done === null`.
  - **Export click handler:** reads current marks with `readDone(storage)`, formats with `formatDoneExport`, creates a
    `Blob` (`application/json`), triggers download via a temporary `<a download="ces-youtube-tools-done-YYYY-MM-DD.json">`,
    and revokes the object URL asynchronously after a short delay.
  - **Import click / file `change` handler:** opens the hidden file input; when a file is chosen, reads it with
    `file.text()`, parses with `parseDoneExport`, applies with `mergeDone`, re-renders via `showMarks()`, displays a
    concise status message (e.g. `Imported 12 done marks (3 new, 20 total).` or the parse/storage error), and resets
    `input.value = ""` so selecting the same file again triggers `change`.
  - Writing to `localStorage` automatically notifies any other open tabs (including guide pages) via the existing
    `storage` event listeners.

## Step 3: Verification

- Run Node JS tests: `node --test tests/js/*.test.mjs`.
- Run Python test suite: `uv run pytest` (ensures static site generation and template checks still pass).
- Manual browser check (Chrome & Firefox):
  - Export with 0 marks and with several marks; inspect the downloaded JSON.
  - Import into a fresh profile / after clearing storage, and import again to verify idempotency.
  - Import an invalid `.json` file (wrong format / corrupt JSON) and verify a clear error is shown and existing marks
    are untouched.
  - Verify an open guide page tab updates its Done toggle automatically via the `storage` event.
  - Verify Export/Import buttons stay hidden when `localStorage` is blocked.
