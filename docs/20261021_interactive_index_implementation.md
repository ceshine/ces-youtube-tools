# Interactive index: implementation plan

Written on 2026-10-21. The design is in "Planned: an interactive index" in [design.md](design.md) (build order items 8
and 9); this file lists the steps to build it. Each step passes the tests and can be deployed on its own.

## Open decisions

- Order: Step 0 first, so the payload's link format is final from the start (recommended), or keep the `.html` links
  and do the index first.
- Scope of Step C: sort, group (channel, month) and a text filter, as below; or also a members-only filter or
  grouping by year.

Decided on 2026-10-21: Step D is required, not optional.

## Step 0: extensionless links (rest of build order item 8)

- `SitePage.filename` stays `<video_id>.html`, the file written. `_index_item` links to `page.video_id` instead of
  `page.filename`. No `href` property: Step B removes `_index_item`, and the script builds the link from the payload.
- The guide page's "All guides" link becomes `./` instead of `index.html` (`_INDEX_LINK` in `guide_html.py`), and the
  `render_guide_page` docstring ("Add a link to `index.html`") says so.
- Update the tests that check for `index.html` links and `.html` hrefs (`test_guide_html.py`, `test_static_site.py`).
- Not yet verified: Cloudflare Pages and `npx wrangler pages dev` serve `/<video_id>` from `<video_id>.html`. Check in
  the browser after deploying, and record it in [implementation_notes.md](implementation_notes.md).

## Step A: write the payload next to the current list

The page looks the same; the data is added and tested.

- `static_site.py`: `index_payload(pages) -> list[dict]` with exactly `video_id`, `title`, `channel`, `upload_date`
  (ISO date or `null`), `duration` (seconds or `null`) and `members_only` (`true`, `false` or `null`).
- `_script_json(data) -> str`: `json.dumps(..., ensure_ascii=False)`, then `<`, `>` and `&` written as
  `\u003c`, `\u003e` and `\u0026`.
- `templates/site_index.html`: `<script type="application/json" id="guides">$guides_json</script>`.
- Tests:
  - A title containing `</script>` stays inside the block, and `json.loads` of the block returns it unchanged.
  - A left-out guide's video ID is nowhere in `index.html`.
  - Each entry has exactly the six keys: no description, no guide text.

## Step B: the script renders the current view, and the list built in Python is removed

- `templates/site_index.mjs`, with the pure functions exported (`export function`):
  - `formatDuration`, the same output as Python's `format_duration` (`H:MM:SS` or `M:SS`, empty when unknown).
  - `compareGuides`, `sortGuides`, `filterGuides` and `groupGuides`.
  - `parseView` and `formatView`, for the URL hash.
- The code that touches the page is a `render(view)` function, started by `if (typeof document !== "undefined")
  main();`, so importing the file in Node has no side effects.
- Rendering uses `createElement` and `textContent` only, and builds the same markup as `_index_item` does now
  (`<ol class="guides">`, `.meta`, `.badge`), so the CSS does not change.
- `write_site` also writes `index.mjs`, read with `importlib.resources`. The wheel already ships every file in the
  package (`packages = ["src/ces_youtube_tools"]` in `pyproject.toml`); a test reads it through `resources`.
- `templates/site_index.html`: `<script type="module" src="index.mjs">` and a `<noscript>` line saying the index needs
  JavaScript. `$listing` goes; `$count` stays, as the text shown before the script runs.
- Remove `_index_item` and the imports only it uses (`html.escape`, `format_duration`, or `ruff check` fails with
  F401); rewrite the `test_render_index_*` tests around the payload. `test_write_site_writes_every_file` lists every
  file written, so it gains `index.mjs`.
- `_newest_first` stays: it keeps the payload in a stable order, so a build with the same guides gives the same
  output. The page does not rely on that order; the script always sorts. A JS test checks that the default sort puts
  newer dates first and no date last; ties within a day follow the title order of Step C, which can differ from
  Python's.
- Tests:
  - `tests/js/site_index.test.mjs`, with `node:test` and `node:assert/strict`.
  - `tests/test_js.py`: skipped when `shutil.which("node")` is `None`; collects `tests/js/*.test.mjs` with `pathlib`,
    runs `node --test` with the files by name, and fails with Node's output.

## Step C: the controls

- Sort: upload date (default), title, channel or length, ascending or descending. Unknown values (no date, no length)
  go last in both directions; ties go by title.
- Titles and channels are compared with `Intl.Collator(undefined, { numeric: true, sensitivity: "base" })`, so "Part
  10" comes after "Part 9" and the order follows the browser's language. This differs slightly from Python's
  `casefold()` order, which only orders the payload. The Node tests pass a fixed locale.
- Group: none (default), channel or month. Each group is `<section><h2>October 2026 · 3</h2><ol>…`. Month groups follow
  the date order, channel groups are alphabetical, and "No date" or "Unknown channel" comes last.
- The month is the first seven characters of `upload_date` (`2026-10`), not a `Date` in local time: `new
  Date("2026-10-01")` is midnight UTC, which is still September west of UTC. The heading is formatted from that
  month with `timeZone: "UTC"`.
- Filter: a text box matching title and channel, ignoring case after `normalize("NFKC")`; every word typed must
  match. Two empty states: "No guides yet." and "No guides match …".
- View in the hash, such as `#sort=date&order=desc&group=month&q=rust`:
  - `parseView` takes each setting on its own: an unknown or missing value becomes that setting's default, and the
    other settings are kept, since the hash can be edited by hand.
  - Changes are written with `history.replaceState`, so typing in the filter does not fill the back-button history.
  - A `hashchange` listener renders the view again when the hash is edited.
- The script writes the count line from the current view, such as "12 of 40 guides, grouped by month, newest upload
  first."

## Step D: remember the view in `localStorage`

The hash keeps the view on reload and when going back from a guide, but the guide page's "All guides" link goes to
`./` without a hash and resets it. The index stores the last view in `localStorage` and restores it when the URL has
no hash. Made a required step on 2026-10-21.

- What is stored: sort, order and grouping, under the key `ces-youtube-tools:index-view` (the guide page uses
  `ces-youtube-tools:scroll-to-player`). The value is the same string `formatView` writes for the hash, such as
  `sort=title&order=asc&group=channel`, so one format and one parser (`parseView`) cover both.
- The filter text is not stored. A filter restored on a later visit would quietly hide guides, including ones added
  since; it stays in the hash, so reloading and going back keep it.
- Which view is used on load, first match wins:
  1. The hash, when it holds any view setting. A saved link or the back button shows the view it names.
  2. The stored view, which is then written into the hash with `history.replaceState`, so the URL shows it.
  3. The default: newest upload first, no grouping.
- Saving: every change of sort, order or grouping writes the stored view, whether it comes from the controls or from
  a `hashchange` (the hash edited by hand, back or forward), since it is the view on screen. When the view equals the
  default, the key is removed instead, so the default can change in a later build without being shadowed by an old
  stored value.
- A "Reset view" button returns to the default (filter cleared) and removes the key. Without it, getting back to the
  default takes up to three changes.
- Storage may be missing or throw (blocked site data, private windows). Every read and write is inside `try`/`catch`,
  as on the guide page; on failure the index works as without Step D. A stored value goes through `parseView` like a
  hash: an unknown value becomes that setting's default, and the other settings are kept.
- Code, as pure functions that the Node tests call with a stub storage object:
  - `initialView(hash, storage)` applies the order above and returns the view and whether the hash must be
    rewritten (true when the stored view was used).
  - `saveView(storage, view)` writes or removes the key.
  - `main()` passes `location.hash` and `window.localStorage`, read inside the `try`, since reading the property
    itself can throw; when it throws, `main()` passes `null`. With `null`, `initialView` gives the hash or the
    default, and `saveView` does nothing.
- Tests (`tests/js/site_index.test.mjs`), through `initialView` and `saveView`:
  - The hash beats the stored view; the stored view beats the default, and asks for the hash to be rewritten.
  - `saveView` leaves out the filter text, and removes the key for the default view.
  - A stub whose `getItem` and `setItem` throw gives the default and no error; so does `null` instead of a storage
    object.
  - A stored value with an unknown sort keeps its stored grouping; one that is not a view setting at all gives the
    default.

## Docs, as each step lands

- [design.md](design.md): mark build order items 8 and 9 done; turn "Planned: an interactive index" into a
  description of what was built; update the `site` bullets about `.html` links, including "Each page gets an 'All
  guides' link to `index.html`".
- [README.md](../README.md): `index.mjs` in "Where files go"; `node --test` under "Development".
- [implementation_notes.md](implementation_notes.md): what the browser shows after deploying (the content type of
  `.mjs`, extensionless URLs), with the date and a command to recheck.
