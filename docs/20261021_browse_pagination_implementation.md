# Browse pagination: implementation plan

Written on 2026-10-21. `browse` lists a channel's latest videos (20 by default, `-n`) in a checkbox menu, and older
videos can only be reached with a larger `-n`. This plan adds "Previous page" and "Next page" entries to that menu.
Videos ticked together with a page entry stay selected: the selection is kept across pages, a page the user returns to
shows its earlier ticks, and unticking a video there drops it. Enter with no page entry ticked writes the guides for
every video selected on any page. The current design is in "Browse step" in [design.md](design.md). Each step passes
the tests and can be committed on its own.

## Open decisions

None left.

Decided on 2026-10-21:

- Page entries go inside the checkbox menu (select with Space, confirm with Enter), not in a separate action menu
  after it. The common case, picking from the first page, then takes the same keys as before.
- `-n` becomes the page size in `browse`. `list` is unchanged.
- No separator line between the videos and the page entries. `pickers.py` numbers every item, and a
  `questionary.Separator` would need numbering that skips it. The entries' labels (`◂`, `▸`) set them apart.
- A video uploaded while the user is browsing can appear at the top of the next page, because each page fetches the
  playlists again. The run is minutes long and the video would be listed anyway, so this is accepted rather than
  guarded against.
- If fetching the next page fails, the error is printed and the current page is shown again, so the selection is
  kept. Checking "Next page" again retries.

## Step 1: `VideoPager` in `youtube.py`

The listing merges up to four upload playlists (`UULF`, `UULV`, and `UUMF`, `UUMV` for members) by approximate
timestamps, so a page is a slice of the merged list, not an offset into one playlist. The first K videos of the
merged list come from the first K of each playlist, so a deeper page needs a deeper `--playlist-end` for each
playlist and a new merge, not a cursor per playlist. yt-dlp reads a playlist's continuation pages from the start
anyway, so `--playlist-start` would not save requests.

- `fetch_playlist` returns a `PlaylistFetch`: the videos, and `full`, whether yt-dlp returned `limit` entries before
  any were dropped (null or ID-less entries). `full` must count the raw entries: counting the videos left would read
  a playlist with one dropped entry as exhausted, and hide its older videos. A missing playlist returns
  `PlaylistFetch(videos=[], full=False)`. Two `fetch_playlist` tests in `tests/test_listing.py`
  (`test_fetch_playlist_parses_entries_and_asks_for_approximate_dates` and
  `test_fetch_playlist_missing_playlist_is_empty`) switch to `.videos` and also check `full`.
- Split the fetch-and-merge part of `list_latest_videos` into a helper, `_fetch_merged(channel, ytdlp, depth)`. It
  fetches each playlist to `depth` and returns the merged videos (live and upcoming streams dropped), and whether any
  playlist was `full` (so it may hold more). `list_latest_videos` becomes `_fetch_merged(...)[0][:limit]`, with the
  same behavior and tests.
- `VideoPager(channel, ytdlp, page_size)`:
  - `page(number) -> list[VideoSummary]`: 1-based. A page already fetched comes from the cache. Page
    `page_count + 1` is fetched. Any other number raises `ValueError`.
  - Fetching a page: depth `shown + page_size + 1`, where `shown` counts the videos on the cached pages. Drop the
    IDs already shown, and the new page is the first `page_size` videos left. Dropping by ID, not slicing
    `[shown:shown + page_size]`, matters because a later fetch can order the videos differently from an earlier
    one: `approximate_date` derives each timestamp from text such as "3 days ago" at fetch time (see
    [implementation_notes.md](implementation_notes.md)), a new upload shifts every video down, and live or upcoming
    entries dropped from inside a playlist let a deeper entry of that playlist move up. A slice could then skip a
    video or show it twice.
  - `has_more` (property): after the last fetch, more videos are left over, or some playlist was `full`. It can be
    wrong in one way only: it says "maybe more" when there are none, for example when the extra entries are
    upcoming streams or duplicates. The next page is then empty, `page` returns `[]` without caching it, and
    `has_more` becomes False.
  - A fetch that raises leaves the pager as it was: the cache, `page_count` and `has_more` change only after the
    fetch and merge succeed, so `browse` can show the current page again and retry.
  - `page_count` (property) and `fetched() -> list[VideoSummary]`, all cached videos in page order, for ordering
    the final selection.
- Tests in `tests/test_listing.py`. `UrlRunner` answers each URL with one fixed response, which cannot test a later
  fetch that orders the videos differently, so add a `SequenceRunner` that answers each URL from a list of
  responses, one per call, and records each call's `--playlist-end`.
  - Page 1 asks each playlist for `page_size + 1` entries, and page 2 for `2 * page_size + 1`.
  - Page 1 again does not call yt-dlp.
  - Skipping a page raises `ValueError`.
  - A second fetch that orders the boundary differently neither skips nor repeats a video.
  - `has_more` is False when every playlist returned fewer than `depth` entries, and becomes False after an empty
    page.
  - A playlist that returned `depth` entries, one of them `{"id": None}`, still counts as `full`.
  - yt-dlp fails on page 2: the pager is unchanged, and the retry returns page 2.
  - Members-only playlists are fetched only when the channel has `members` set (as for `list_latest_videos`).
- Implementation note, marked "not yet verified": how many entries yt-dlp reads per continuation request for `UU…`
  playlists (expected about 100, which would make pages 1 to 4 at the default size one request per playlist), with a
  command to recheck it, for example
  `$Y -v --flat-playlist --playlist-end 250 --print id "https://www.youtube.com/playlist?list=UULF$ID" 2>&1 | grep -i page`.

## Step 2: `pick_many` keeps ticks and can allow an empty answer

- `pick_many(message, options, checked: set[int] | None = None, require_selection: bool = True)`:
  - `checked`: 1-based numbers of the items shown ticked (`questionary.Choice(checked=True)`), so a page the user
    returns to shows its earlier ticks.
  - `require_selection`: when False, an empty answer is accepted. `browse` passes False when videos are already
    selected on other pages, so the user can confirm from a page where nothing is ticked.
- `_items` gains the `checked` argument. `pick_one` is unchanged.
- Tests in `tests/test_pickers.py` with the `asked` fixture: the ticked choices, and that `validate` accepts `[]`
  only when `require_selection` is False.

## Step 3: the page loop in `browse`

- Replace the listing and the `pick_many` call in `browse` with `_pick_videos(pager, channel_name) ->
  list[VideoSummary] | None` (None when the user presses Ctrl+C). Its loop:
  - Fetch the page under `CONSOLE.status(...)` when it is not cached. An empty first page prints "No videos found"
    as today. An empty later page (`has_more` was wrong) prints "No older videos." and shows the current page
    again, now without "Next page", with the selection kept.
  - Labels: the videos' `video_choice_label`s, then `◂ Previous page (videos 1–20)` on pages after the first, and
    `▸ Next page (older videos)` when `number < pager.page_count` or `pager.has_more`. The next page's range is not
    known before it is fetched, so it is not shown.
  - Title: `Latest videos of NAME` on page 1 and `Videos 21–40 of NAME` after it, then
    `(N selected on other pages)` when there are any, and the existing "Space selects, Enter confirms" hint.
  - The selection is a `dict[str, VideoSummary]` by video ID. After each answer, and before acting on a page entry,
    the page's ticked videos are added and its unticked ones removed. So videos ticked together with a page entry
    stay selected, including when both page entries are ticked and the page is shown again.
  - Both page entries ticked: print a hint to untick the page entries, and show the same page again. This also
    happens after questionary's `a` (toggle all) or `i` (invert), which tick the page entries too, so the hint says
    so. One ticked: move to that page. None ticked: return the selected videos in `pager.fetched()` order (newest
    first across pages), so `[n/total]` and the summary table keep their order.
  - `YtDlpError` on page 1 fails the command as today. On a later page, the error is printed and the current page is
    shown again (see "Decided on 2026-10-21").
- `-n` help becomes "Number of videos per page."
- `browse` builds the pager with `VideoPager(found, _make_ytdlp(config, with_cookies=False), limit)`, and tests
  replace `cli.VideoPager` with a fake that serves pages from lists.
- Tests in `tests/test_cli.py`. `FakePickers.pick_many` takes a list of answers (one per menu) and records the
  `checked` and `require_selection` it was given.
  - Existing tests that replace `cli.list_latest_videos` for `browse` move to the fake pager: the `browse_setup`
    fixture, `test_browse_video_labels_fit_beside_the_checkbox` and `test_browse_needs_a_terminal`. So does
    `test_browse_quit`, which sets `pickers.videos = None`. The fake pager keeps `browse_setup`'s check that the
    listing sends no cookies (`ytdlp.cookies_spec is None`).
  - Pick on page 1, go to page 2, pick, confirm: both guides are written, newest first.
  - Going back to page 1 shows its earlier ticks.
  - Unticking a video on a revisited page drops it from the selection.
  - No "Next page" entry when the pager has no more videos, and no "Previous page" entry on page 1.
  - Both page entries ticked shows the same page again, with the selection kept.
  - An empty later page prints "No older videos." and shows the current page again without "Next page".
  - A later page's fetch error keeps the selection and shows the current page again.
  - Ctrl+C on page 2 prints "Bye." and exits 0.
- Docs in the same commit:
  - [design.md](design.md): step 3 of "Browse step"; the paging design (fetch deeper, drop shown IDs, what
    `has_more` means) in "Video listing"; "claimed in menu order" in "Browse step" becomes "claimed in listing order
    (newest first across pages)"; and an entry in "Build order".
  - README: the `browse` lines, including that `a` and `i` also tick the page entries.
