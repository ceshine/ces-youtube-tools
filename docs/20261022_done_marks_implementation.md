# Done marks: implementation plan

Written on 2026-10-22. The reader marks a guide "Done" on its page or in the index, and the index can show only the
guides still to do. The marks are kept in the browser's `localStorage`. The design is in "Done marks" in
[design.md](design.md) (build order item 11); this file lists the steps to build it. Each step passes the tests and
can be deployed on its own: Step 1 adds a script nothing loads yet, Step 2 gives the guide pages their toggles, and
Step 3 the index. All steps were done on 2026-10-22.

## Open decisions

None left.

Decided on 2026-10-22:

- One boolean per `video_id`, labelled "Done", with no time kept. Marked by hand only, both ways.
- `localStorage`, one key, `ces-youtube-tools:done`, holding a JSON array of video IDs. No Pages Function or KV store,
  no export or import, and nothing in the CLI.
- Toggles on the guide page (next to "All guides" and after the guide's text) and on every index row.
- Wording: the button's name is always "Done"; a `○` or `✓` glyph and `aria-pressed` show the state. The index row's
  button is the glyph alone, named "Done" with `aria-label`; a done row is dimmed, with no separate badge. The index
  setting is "Show: All | To do" (`show=all`, `show=todo`), "All" by default.
- "Show" is remembered in `localStorage` with the sort, order and grouping.
- A row marked done while "To do" is shown stays until the next render.
- Group headings keep their count, with no done count.
- Both pages read the marks again on `pageshow` and on the `storage` event.
- The shared code is one module, `templates/site_done.mjs`. It is written to `OUT` as `site_done.mjs`, not as
  `done.mjs` as first planned: `site_index.mjs` imports it with a relative path, which must find the file both in
  `OUT`, where the browser loads `index.mjs`, and in `templates/`, where the Node tests import `site_index.mjs`.

## Step 1: `site_done.mjs`, its tests, and `site` writing it

- `src/ces_youtube_tools/templates/site_done.mjs`, an ES module laid out like `site_index.mjs`: pure functions first,
  exported and documented with JSDoc, then the code that touches the page, which runs only when called (the module
  has no top-level side effects, so Node can import it).
  - `DONE_KEY = "ces-youtube-tools:done"`.
  - `parseDone(text) -> Set<string>`: `null` (no key) is an empty set. Throws for text that is not JSON, or JSON that
    is not an array of strings.
  - `formatDone(done) -> string`: a JSON array, sorted, so the same marks always give the same value.
  - `readDone(storage) -> Set<string> | null`: null when `storage` is null or `getItem` throws (the toggles are then
    hidden). A value `parseDone` rejects gives an empty set and a `console.warn`.
  - `setDone(storage, videoId, done) -> boolean`: reads the key again, adds or removes the ID, and writes it with
    `formatDone`, or removes the key when no ID is left. Returns false, with a `console.warn`, if reading or writing
    throws (a full quota, blocked site data); storage is then unchanged.
  - `browserStorage() -> Storage | null`: moved from `site_index.mjs`, which imports it from here. Reading
    `window.localStorage` can throw, so it returns null instead, with a warning that nothing will be remembered.
  - Page code: `showToggle(button, done)` sets `aria-pressed` and the glyph in the button's `.done-mark` span (`✓` or
    `○`), and nothing else, so the button's name stays as written. `initGuidePage(storage)` (used in Step 2).
- `static_site.write_site` writes the module as `site_done.mjs`, next to `index.mjs`. The site's file list in the
  `write_site` docstring, "`site` command" in [design.md](design.md) and the README's "Site" line name it.
- Node tests in `tests/js/site_done.test.mjs`, with a `FakeStorage` (a `Map` behind `getItem`, `setItem` and
  `removeItem`) and a storage whose methods throw:
  - `parseDone`: null is empty; a valid array; text that is not JSON, a JSON object, and an array holding a number
    each throw.
  - `formatDone` sorts.
  - `readDone`: null storage and a throwing `getItem` give null; a malformed value gives an empty set and warns
    (`t.mock.method(console, "warn")`).
  - `setDone`: adds and removes; removes the key with the last ID; keeps an ID that another tab wrote between the
    page's read and its toggle; replaces a malformed value; a throwing `setItem` returns false and leaves the value
    as it was.
- pytest in `tests/test_static_site.py`: `site_done.mjs` is written and equals the template. A second test reads every
  `.mjs` file written to `OUT` and checks that each relative `import ... from "./x"` names a file in `OUT`, so a
  rename on one side cannot break the site without failing a test.

## Step 2: toggles on the guide page

- `guide_page.html`:
  - `$index_link` becomes the nav with the toggle: `<a href="./">← All guides</a>` followed by
    `<button type="button" class="done-toggle" data-video-id="..." aria-pressed="false" hidden><span
    class="done-mark" aria-hidden="true">○</span> Done</button>`. `_INDEX_LINK` in `guide_html.py` becomes a
    function of the video ID (validated already by `render_guide_page`).
  - A new placeholder, `$done_end`, right after `$body` inside `<main>`: the same button in a `<p class="done-end">`,
    then `<script type="module">import { browserStorage, initGuidePage } from "./site_done.mjs";
    initGuidePage(browserStorage());</script>`. Empty unless `link_to_index`, like `$index_link`, since only `site`
    writes `site_done.mjs`. If the module cannot load, the buttons stay hidden.
  - Styles for `.done-toggle` (the look of the existing controls: UI font, 14px, a bordered button; pressed uses the
    accent color) and `.done-end`; the toggles are hidden in `@media print`.
- `initGuidePage(storage)`: finds the `.done-toggle` buttons. If `readDone` gives null, it leaves them hidden.
  Otherwise it shows them with `showToggle`, and a click on either calls `setDone` with the opposite of its
  `aria-pressed`; on success both buttons are updated, on failure neither. It reads the marks again and updates both
  buttons on `pageshow` and on a `storage` event whose key is `DONE_KEY` or null (storage cleared).
- `render_guide_page`'s docstring says that `link_to_index` also adds the done toggles and loads `site_done.mjs`.
- pytest in `tests/test_guide_html.py`: with `link_to_index=True`, the page has two `done-toggle` buttons, both
  `hidden`, with the page's `data-video-id`, and the module script imports `./site_done.mjs`; without it, there is no
  `done-toggle` and no `site_done.mjs` in the page.
- Node tests: `initGuidePage` touches the page and is not tested in Node (no DOM there, and `site_index.mjs`'s page
  code is not tested either). It is checked by hand: in Firefox and in Chrome, with `npx wrangler pages dev _site`,
  toggle on one page, reload, open the same guide in a second tab, Back from the index to the guide.
- [design.md](design.md): "Guide pages" or "`site` command" mentions the two toggles.

## Step 3: the index

- `site_index.mjs`:
  - `CHOICES.show = { all: "All", todo: "To do" }`, `DEFAULT_VIEW.show = "all"`. `parseView`, `initialView`,
    `saveView` and "Reset view" go over `CHOICES` already, so they take `show` without other changes; `formatView`
    names its settings one by one and gains `show`. A stored view from before this step has no `show` and reads as
    "All".
  - `showGuides(guides, view, done) -> object[]` (pure, exported): with `show=todo` and a `done` set, the guides not in
    it; otherwise all of them. `render` applies it after `filterGuides`.
  - `describeView(view, shown, total)`: with `show=todo`, "12 of 40 guides to do, grouped by month, …" (and "40 guides
    to do" when nothing is done).
  - Empty list: "No guides yet." as before; with `show=todo` and no filter text, "Nothing left to do."; with filter
    text, `No guides to do match “…”.` or the current message.
  - `isFiltering` is unchanged: "To do" does not open the groups.
  - `render` reads the marks with `readDone(storage)` on every render and passes them to `guideItem`. With marks
    available, each row starts with `<button type="button" class="done-toggle" aria-label="Done" aria-pressed="…"
    data-video-id="…"><span class="done-mark" aria-hidden="true">…</span></button>`, and a done row has the class
    `done`. Without storage (`readDone` gives null), there is no button and the "Show" row stays hidden.
  - One click listener on `#listing` handles every row's button: `setDone`, then, on success, `showToggle` and the
    row's `done` class are changed in place. It does not render, so a row marked done under "To do" stays until the
    next render.
  - `pageshow` and a `storage` event whose key is `DONE_KEY` or null render the current view again (`readControls`),
    guarded like the other listeners, without writing the hash or the stored view.
- `site_index.html`: a fourth `.choice` row, `show-label` "Show" with `show-choices`, after the grouping; it starts
  `hidden`, and the script shows it when there are marks to read. Styles: `.done-toggle` as a small round icon button
  at the start of the row (a flex row with the link and its meta beside it), `.guides li.done` dims the link and the
  meta but not the button, and the existing `prefers-reduced-motion` rule covers any transition.
- Node tests in `tests/js/site_index.test.mjs`: `CHOICES.show` and `DEFAULT_VIEW.show`; `parseView` reads `show=todo`
  and falls back to "all" for an unknown value; `formatView` writes `show`; `initialView` restores `show=todo` from
  storage, and a stored view without `show` gives "all"; `saveView` stores `show=todo` and removes the key for the
  default; `showGuides` with "all", with "todo", and with no marks (null); `describeView` with "todo".
- pytest in `tests/test_static_site.py`: the index page has the `show-choices` row, `hidden`.
- Docs in the same commit: the README's site paragraph (marking guides done, "Show: All | To do", that marks stay in
  this browser); in [design.md](design.md), "Interactive index" lists the "Show" row among the controls, its
  parenthesis about the stored "Show" setting loses the word "planned", and "Done marks" and build order item 11 say
  "Done" with the date.
