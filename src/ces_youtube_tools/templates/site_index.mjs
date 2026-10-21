// The index page's script, written to the site as `index.mjs`. It reads the guides from the page's JSON block and
// renders the view named in the URL hash, such as `#sort=generated&group=month&q=rust`.
//
// The pure functions are exported for the Node tests in `tests/js/`. Only `main()` touches the page, and it runs only
// when there is a `document`, so importing this file in Node has no side effects.
//
// Days: `published` and `generated` are UTC times, shown and sorted as a day in the browser's time zone. Each is
// turned into a `YYYY-MM-DD` string first (`localDay`); a date-only value (`upload_date`, an old `generated`) is used
// as it is, never through `Date`, since `new Date("2026-10-01")` is midnight UTC, which is still September west of UTC.

export const DEFAULT_VIEW = Object.freeze({ sort: "published", order: "desc", group: "none", q: "" });

const SORTS = ["published", "generated"];
const ORDERS = ["desc", "asc"];
const GROUPS = ["none", "channel", "month"];
const SORT_LABELS = {
  published: { desc: "newest publish date first", asc: "oldest publish date first" },
  generated: { desc: "newest generation date first", asc: "oldest generation date first" },
};

/**
 * Format a length in seconds as `H:MM:SS` or `M:SS`, as Python's `format_duration` does.
 *
 * @param {number | null} seconds The length, or null if unknown.
 * @returns {string} The formatted length, or an empty string if unknown.
 */
export function formatDuration(seconds) {
  if (seconds === null || seconds === undefined) {
    return "";
  }
  const total = Math.trunc(seconds);
  const hours = Math.trunc(total / 3600);
  const minutes = Math.trunc(total / 60) % 60;
  const secs = String(total % 60).padStart(2, "0");
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${secs}` : `${minutes}:${secs}`;
}

/**
 * Turn a payload time or date into a day in the browser's time zone.
 *
 * @param {string | null} value A UTC time such as `2026-10-14T16:03:12Z`, a date such as `2026-10-01`, or null.
 * @returns {string | null} The day as `YYYY-MM-DD`: a date as it is, a time as the local day. Null if unknown or
 *     unreadable.
 */
export function localDay(value) {
  if (value === null || value === undefined) {
    return null;
  }
  if (!value.includes("T")) {
    return value;
  }
  const time = new Date(value);
  if (Number.isNaN(time.getTime())) {
    return null;
  }
  const month = String(time.getMonth() + 1).padStart(2, "0");
  const day = String(time.getDate()).padStart(2, "0");
  return `${time.getFullYear()}-${month}-${day}`;
}

/**
 * The day a guide is sorted and grouped by.
 *
 * @param {object} guide A payload entry.
 * @param {string} sort `published` (the publish day, else the upload date) or `generated`.
 * @returns {string | null} The day as `YYYY-MM-DD`, or null if unknown.
 */
export function guideDay(guide, sort) {
  if (sort === "generated") {
    return localDay(guide.generated);
  }
  return localDay(guide.published) ?? guide.upload_date;
}

/**
 * Make the collator titles and channels are compared with: "Part 10" after "Part 9", case and accents ignored.
 *
 * @param {string | undefined} locale The locale; undefined for the browser's language.
 * @returns {Intl.Collator} The collator.
 */
export function makeCollator(locale) {
  return new Intl.Collator(locale, { numeric: true, sensitivity: "base" });
}

function compareText(a, b, collator) {
  if (a === b) {
    return 0;
  }
  if (a === null) {
    return 1;
  }
  if (b === null) {
    return -1;
  }
  return collator.compare(a, b);
}

function channelOf(guide) {
  return guide.channel || null;
}

/**
 * Compare two guides for a view: by day in the view's order, then by channel, then by title.
 *
 * Channel and title stay A to Z when the order is reversed, and unknown values go last in both orders.
 *
 * @param {object} a A payload entry.
 * @param {object} b Another payload entry.
 * @param {object} view The view; its `sort` and `order` are used.
 * @param {Intl.Collator} collator From `makeCollator`.
 * @returns {number} Negative if `a` comes first, positive if `b` does, 0 if they tie.
 */
export function compareGuides(a, b, view, collator) {
  const dayA = guideDay(a, view.sort);
  const dayB = guideDay(b, view.sort);
  if (dayA !== dayB) {
    if (dayA === null) {
      return 1;
    }
    if (dayB === null) {
      return -1;
    }
    const older = dayA < dayB ? -1 : 1;
    return view.order === "desc" ? -older : older;
  }
  return compareText(channelOf(a), channelOf(b), collator) || compareText(a.title, b.title, collator);
}

/**
 * Sort guides for a view.
 *
 * @param {object[]} guides The payload entries; not changed.
 * @param {object} view The view.
 * @param {string | undefined} locale The locale for comparing text; undefined for the browser's language.
 * @returns {object[]} The guides in a new array, in the view's order.
 */
export function sortGuides(guides, view, locale) {
  const collator = makeCollator(locale);
  return guides.toSorted((a, b) => compareGuides(a, b, view, collator));
}

function fold(text) {
  return text.normalize("NFKC").toLowerCase();
}

/**
 * Keep the guides whose title or channel contains every word of the filter text, ignoring case.
 *
 * @param {object[]} guides The payload entries.
 * @param {string} text The filter text; empty keeps every guide.
 * @returns {object[]} The matching guides, in the same order.
 */
export function filterGuides(guides, text) {
  const words = fold(text)
    .split(/\s+/)
    .filter((word) => word !== "");
  return guides.filter((guide) => {
    const haystack = fold(`${guide.title} ${guide.channel ?? ""}`);
    return words.every((word) => haystack.includes(word));
  });
}

/**
 * Split sorted guides into the view's groups.
 *
 * Month groups follow the order of the guides, which is the date order, so "No date" comes last; a guide's month is
 * that of the day the view sorts by. Channel groups are alphabetical, with "Unknown channel" last; they go by the
 * exact name, so names that differ only in case or accents, which YouTube treats as different channels, are separate
 * groups. Within a group, the guides keep their order.
 *
 * @param {object[]} guides The payload entries, sorted for the view.
 * @param {object} view The view; its `group` and `sort` are used.
 * @param {string | undefined} locale The locale for month names and for comparing channels; undefined for the
 *     browser's language.
 * @returns {{label: string | null, guides: object[]}[]} The groups. Without grouping, one group with a null label.
 */
export function groupGuides(guides, view, locale) {
  if (view.group === "channel") {
    const collator = makeCollator(locale);
    const groups = [...Map.groupBy(guides, channelOf)].toSorted(([a], [b]) => compareText(a, b, collator));
    return groups.map(([channel, items]) => ({ label: channel ?? "Unknown channel", guides: items }));
  }
  if (view.group === "month") {
    // Formatted in UTC from the first of the month, so the month name does not depend on the time zone.
    const months = new Intl.DateTimeFormat(locale, { year: "numeric", month: "long", timeZone: "UTC" });
    const groups = Map.groupBy(guides, (guide) => guideDay(guide, view.sort)?.slice(0, 7) ?? null);
    return [...groups].map(([month, items]) => ({
      label: month === null ? "No date" : months.format(new Date(`${month}-01T00:00:00Z`)),
      guides: items,
    }));
  }
  return [{ label: null, guides }];
}

function pick(value, allowed, fallback) {
  return allowed.includes(value) ? value : fallback;
}

/**
 * Read a view from the URL hash.
 *
 * Each setting is read on its own, since the hash can be edited by hand: an unknown or missing value becomes that
 * setting's default, and the other settings are kept.
 *
 * @param {string} hash The hash, such as `#sort=generated&group=month`, with or without the `#`.
 * @returns {{sort: string, order: string, group: string, q: string}} The view.
 */
export function parseView(hash) {
  const params = new URLSearchParams(hash.replace(/^#/, ""));
  return {
    sort: pick(params.get("sort"), SORTS, DEFAULT_VIEW.sort),
    order: pick(params.get("order"), ORDERS, DEFAULT_VIEW.order),
    group: pick(params.get("group"), GROUPS, DEFAULT_VIEW.group),
    q: params.get("q") ?? "",
  };
}

/**
 * Write a view as `parseView` reads it, without the `#`.
 *
 * @param {object} view The view.
 * @returns {string} Such as `sort=published&order=desc&group=month&q=rust`; `q` only when there is filter text.
 */
export function formatView(view) {
  const params = new URLSearchParams({ sort: view.sort, order: view.order, group: view.group });
  if (view.q !== "") {
    params.set("q", view.q);
  }
  return params.toString();
}

/**
 * Describe what the list shows, for the line under the heading.
 *
 * @param {object} view The view.
 * @param {number} shown The guides that match the filter.
 * @param {number} total All the guides.
 * @returns {string} Such as "12 of 40 guides, grouped by month, newest publish date first."
 */
export function describeView(view, shown, total) {
  const all = total === 1 ? "1 guide" : `${total} guides`;
  const count = shown === total ? all : `${shown} of ${all}`;
  const grouping = view.group === "none" ? "" : `, grouped by ${view.group}`;
  return `${count}${grouping}, ${SORT_LABELS[view.sort][view.order]}.`;
}

// The code below touches the page. Text from the payload goes in with `textContent` only, never `innerHTML`: titles
// and channel names come from YouTube.

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) {
    node.className = className;
  }
  if (text !== undefined) {
    node.textContent = text;
  }
  return node;
}

function timeElement(day) {
  const node = element("time", null, day);
  node.dateTime = day;
  return node;
}

function guideItem(guide, view) {
  const link = element("a", null, guide.title);
  link.setAttribute("href", guide.video_id);
  const meta = element("p", "meta");
  const published = guideDay(guide, "published");
  if (published !== null) {
    meta.append(timeElement(published));
  }
  const generated = guideDay(guide, "generated");
  if (view.sort === "generated" && generated !== null) {
    const span = element("span", "generated", "Generated ");
    span.append(timeElement(generated));
    meta.append(span);
  }
  if (channelOf(guide) !== null) {
    meta.append(element("span", "channel", guide.channel));
  }
  if (guide.duration !== null) {
    meta.append(element("span", "length", formatDuration(guide.duration)));
  }
  if (guide.members_only === true) {
    meta.append(element("span", "badge", "Members only"));
  } else if (guide.members_only === null) {
    meta.append(element("span", "badge", "Availability unknown"));
  }
  const item = element("li");
  item.append(link, meta);
  return item;
}

function guideList(guides, view) {
  const list = element("ol", "guides");
  list.append(...guides.map((guide) => guideItem(guide, view)));
  return list;
}

function listing(guides, shown, view) {
  if (guides.length === 0) {
    return [element("p", "empty", "No guides yet.")];
  }
  if (shown.length === 0) {
    return [element("p", "empty", `No guides match “${view.q}”.`)];
  }
  return groupGuides(shown, view).map((group) => {
    if (group.label === null) {
      return guideList(group.guides, view);
    }
    const section = element("section", "group");
    section.append(element("h2", null, `${group.label} · ${group.guides.length}`), guideList(group.guides, view));
    return section;
  });
}

function render(guides, view) {
  const shown = sortGuides(filterGuides(guides, view.q), view);
  document.getElementById("count").textContent = describeView(view, shown.length, guides.length);
  document.getElementById("listing").replaceChildren(...listing(guides, shown, view));
}

function main() {
  try {
    const guides = JSON.parse(document.getElementById("guides").textContent);
    render(guides, parseView(location.hash));
  } catch (error) {
    // Such as a browser without `Map.groupBy`. Say so instead of leaving the list empty; the console keeps the error.
    const note = element("p", "empty", "The list of guides could not be shown; the browser console has the error.");
    document.getElementById("listing").replaceChildren(note);
    throw error;
  }
}

if (typeof document !== "undefined") {
  main();
}
