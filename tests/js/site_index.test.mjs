// Tests for the index page's script. tests/test_js.py runs this file with TZ set to Asia/Taipei and to
// America/Los_Angeles, so local days are checked on both sides of UTC. Times between 07:00Z and 16:00Z fall on the
// same day in both zones; tests that depend on the zone read the expected day from DAY_OF_EVENING_UTC.
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  DEFAULT_VIEW,
  localDay,
  parseView,
  formatView,
  sortGuides,
  groupGuides,
  describeView,
  filterGuides,
  formatDuration,
} from "../../src/ces_youtube_tools/templates/site_index.mjs";

const LOCALE = "en";
const EVENING_UTC = "2026-10-08T16:30:00Z";
const DAY_OF_EVENING_UTC = { "Asia/Taipei": "2026-10-09", "America/Los_Angeles": "2026-10-08" }[process.env.TZ];
const NEEDS_ZONE = { skip: DAY_OF_EVENING_UTC === undefined && "TZ is not one of the zones tests/test_js.py sets" };

function guide(video_id, fields = {}) {
  return {
    video_id,
    title: video_id,
    channel: null,
    upload_date: null,
    published: null,
    duration: null,
    members_only: false,
    generated: null,
    ...fields,
  };
}

function view(fields = {}) {
  return { ...DEFAULT_VIEW, ...fields };
}

function ids(guides) {
  return guides.map((entry) => entry.video_id);
}

test("formatDuration matches Python's format_duration", () => {
  assert.equal(formatDuration(null), "");
  assert.equal(formatDuration(0), "0:00");
  assert.equal(formatDuration(59), "0:59");
  assert.equal(formatDuration(600), "10:00");
  assert.equal(formatDuration(61.9), "1:01");
  assert.equal(formatDuration(3600), "1:00:00");
  assert.equal(formatDuration(3725), "1:02:05");
});

test("localDay keeps a date and turns a time into the local day", () => {
  assert.equal(localDay(null), null);
  assert.equal(localDay("2026-10-01"), "2026-10-01");
  assert.equal(localDay("2026-10-08T12:00:00Z"), "2026-10-08");
  assert.equal(localDay("not a time T"), null);
});

test("localDay uses the browser's time zone", NEEDS_ZONE, () => {
  assert.equal(localDay(EVENING_UTC), DAY_OF_EVENING_UTC);
});

test("the default sort puts newer publish days first, then goes by channel and title", () => {
  const guides = [
    guide("no-date", { channel: "Alpha" }),
    guide("beta-early", { channel: "Beta", title: "A", published: "2026-10-08T08:00:00Z" }),
    guide("alpha-late", { channel: "Alpha", title: "Z", published: "2026-10-08T15:00:00Z" }),
    guide("part-10", { channel: "alpha", title: "Part 10", published: "2026-10-08T09:00:00Z" }),
    guide("part-9", { channel: "Alpha", title: "part 9", published: "2026-10-08T10:00:00Z" }),
    guide("no-channel", { title: "A", published: "2026-10-08T11:00:00Z" }),
    guide("uploaded", { channel: "Zeta", upload_date: "2026-10-10" }),
  ];
  const sameDay = ["part-9", "part-10", "alpha-late", "beta-early", "no-channel"];
  assert.deepEqual(ids(sortGuides(guides, view(), LOCALE)), ["uploaded", ...sameDay, "no-date"]);
  assert.deepEqual(ids(sortGuides(guides, view({ order: "asc" }), LOCALE)), [...sameDay, "uploaded", "no-date"]);
  assert.equal(guides[0].video_id, "no-date", "the input is not changed");
});

test("the publish day is the local day, not the UTC one", NEEDS_ZONE, () => {
  const evening = guide("evening", { channel: "Alpha", published: EVENING_UTC });
  const uploaded = guide("uploaded", { channel: "Zeta", upload_date: "2026-10-09" });
  // On the same day the channel decides; on an earlier day the evening guide comes second.
  const expected = DAY_OF_EVENING_UTC === "2026-10-09" ? ["evening", "uploaded"] : ["uploaded", "evening"];
  assert.deepEqual(ids(sortGuides([uploaded, evening], view(), LOCALE)), expected);
});

test("the generation sort uses the generation day, a date-only value as it is", () => {
  const guides = [
    guide("unknown", { published: "2026-10-20T12:00:00Z" }),
    guide("date-only", { generated: "2026-10-01" }),
    guide("timed", { generated: "2026-10-14T12:00:00Z" }),
  ];
  assert.deepEqual(ids(sortGuides(guides, view({ sort: "generated" }), LOCALE)), ["timed", "date-only", "unknown"]);
  const ascending = view({ sort: "generated", order: "asc" });
  assert.deepEqual(ids(sortGuides(guides, ascending, LOCALE)), ["date-only", "timed", "unknown"]);
});

test("filterGuides needs every word in the title or channel, ignoring case and width", () => {
  const guides = [
    guide("rust", { title: "Ｒｕｓｔ in production", channel: "Systems Talk" }),
    guide("go", { title: "Go in production", channel: null }),
  ];
  assert.deepEqual(ids(filterGuides(guides, "")), ["rust", "go"]);
  assert.deepEqual(ids(filterGuides(guides, "  PRODUCTION ")), ["rust", "go"]);
  assert.deepEqual(ids(filterGuides(guides, "rust talk")), ["rust"]);
  assert.deepEqual(ids(filterGuides(guides, "rust go")), []);
});

test("month groups follow the date order, with no date last", () => {
  const monthView = view({ group: "month" });
  const guides = sortGuides(
    [
      guide("none"),
      guide("september", { published: "2026-09-30T12:00:00Z" }),
      guide("first-of-october", { upload_date: "2026-10-01" }),
      guide("october", { published: "2026-10-08T12:00:00Z" }),
    ],
    monthView,
    LOCALE,
  );
  const groups = groupGuides(guides, monthView, LOCALE);
  assert.deepEqual(
    groups.map((group) => [group.label, ids(group.guides)]),
    [
      ["October 2026", ["october", "first-of-october"]],
      ["September 2026", ["september"]],
      ["No date", ["none"]],
    ],
  );
});

test("month groups use the date the view sorts by", () => {
  const generatedView = view({ sort: "generated", group: "month" });
  const entry = guide("one", { published: "2026-10-08T12:00:00Z", generated: "2026-11-02" });
  assert.deepEqual(
    groupGuides([entry], generatedView, LOCALE).map((group) => group.label),
    ["November 2026"],
  );
});

test("channel groups are alphabetical, with the unknown channel last", () => {
  const guides = [
    guide("first", { channel: "Talks 10" }),
    guide("unknown", { channel: null }),
    guide("second", { channel: "Talks 9" }),
    guide("empty", { channel: "" }),
    guide("third", { channel: "Talks 10" }),
  ];
  const groups = groupGuides(guides, view({ group: "channel" }), LOCALE);
  assert.deepEqual(
    groups.map((group) => [group.label, ids(group.guides)]),
    [
      ["Talks 9", ["second"]],
      ["Talks 10", ["first", "third"]],
      ["Unknown channel", ["unknown", "empty"]],
    ],
  );
});

test("without grouping, there is one group without a label", () => {
  const guides = [guide("one"), guide("two")];
  assert.deepEqual(groupGuides(guides, view(), LOCALE), [{ label: null, guides }]);
});

test("parseView reads each setting on its own", () => {
  assert.deepEqual(parseView(""), DEFAULT_VIEW);
  assert.deepEqual(parseView("#"), DEFAULT_VIEW);
  assert.deepEqual(parseView("#sort=generated&order=asc&group=channel&q=rust+talk"), {
    sort: "generated",
    order: "asc",
    group: "channel",
    q: "rust talk",
  });
  assert.deepEqual(parseView("sort=bogus&order=sideways&group=month"), view({ group: "month" }));
});

test("formatView writes what parseView reads", () => {
  assert.equal(formatView(DEFAULT_VIEW), "sort=published&order=desc&group=none");
  const filtered = view({ sort: "generated", group: "month", q: "a & b #1" });
  assert.deepEqual(parseView(`#${formatView(filtered)}`), filtered);
});

test("describeView says what the list shows", () => {
  assert.equal(describeView(view(), 1, 1), "1 guide, newest publish date first.");
  assert.equal(describeView(view(), 0, 0), "0 guides, newest publish date first.");
  const grouped = view({ sort: "generated", order: "asc", group: "month" });
  assert.equal(describeView(grouped, 1, 3), "1 of 3 guides, grouped by month, oldest generation date first.");
});
