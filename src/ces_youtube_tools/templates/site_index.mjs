// The index page's script, written to the site as `index.mjs`. It reads the guides from the page's JSON block and
// renders the view named in the URL hash, such as `#sort=generated&group=month&q=rust`. Without one, it renders the
// sort, order and grouping it remembered in `localStorage`.
//
// The pure functions are exported for the Node tests in `tests/js/`. Only `main()` touches the page, and it runs only
// when there is a `document`, so importing this file in Node has no side effects.
//
// Days: `published` and `generated` are UTC times, shown and sorted as a day in the browser's time zone. Each is
// turned into a `YYYY-MM-DD` string first (`localDay`); a date-only value (`upload_date`, an old `generated`) is used
// as it is, never through `Date`, since `new Date("2026-10-01")` is midnight UTC, which is still September west of UTC.

export const DEFAULT_VIEW = Object.freeze({ sort: "published", order: "desc", group: "none", q: "" });

// Each setting's choices with their labels, in the order the controls list them. `parseView` accepts only these, and
// the controls are filled from them.
export const CHOICES = Object.freeze({
  sort: Object.freeze({ published: "Publish date", generated: "Generation date" }),
  order: Object.freeze({ desc: "Newest first", asc: "Oldest first" }),
  group: Object.freeze({ none: "None", channel: "Channel", month: "Month" }),
});
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
 * Whether the filter text holds a word, which is when `filterGuides` can leave guides out.
 *
 * @param {object} view The view; its `q` is used.
 * @returns {boolean} True if the filter has a word; spaces alone are no filter.
 */
export function isFiltering(view) {
  return view.q.trim() !== "";
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

/**
 * The key a group is remembered as open under, so a channel and a month with the same label stay apart.
 *
 * @param {object} view The view; its `group` is used.
 * @param {string} label The group's label.
 * @returns {string} Such as `channel:Talks 9`.
 */
export function groupKey(view, label) {
  return `${view.group}:${label}`;
}

/**
 * Whether a group starts open. Groups start closed, so the list reads as a table of contents, except while the filter
 * has a word (`isFiltering`): then every group is open, so the matches are on screen.
 *
 * @param {object} view The view; its `group` and `q` are used.
 * @param {string} label The group's label.
 * @param {Set<string>} openGroups The keys (`groupKey`) of the groups the reader opened.
 * @returns {boolean} True if the group starts open.
 */
export function isGroupOpen(view, label, openGroups) {
  return isFiltering(view) || openGroups.has(groupKey(view, label));
}

function pick(value, choices, fallback) {
  return value !== null && Object.hasOwn(choices, value) ? value : fallback;
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
    sort: pick(params.get("sort"), CHOICES.sort, DEFAULT_VIEW.sort),
    order: pick(params.get("order"), CHOICES.order, DEFAULT_VIEW.order),
    group: pick(params.get("group"), CHOICES.group, DEFAULT_VIEW.group),
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

// The key `localStorage` keeps the last sort, order and grouping under, as `formatView` writes them. The guide page
// keeps its toggle under `ces-youtube-tools:scroll-to-player`.
export const STORAGE_KEY = "ces-youtube-tools:index-view";

function namesAny(text, names) {
  const params = new URLSearchParams(text.replace(/^#/, ""));
  return names.some((name) => params.has(name));
}

function readStored(storage) {
  if (storage === null) {
    return null;
  }
  try {
    return storage.getItem(STORAGE_KEY);
  } catch (error) {
    console.warn("The remembered view could not be read.", error);
    return null;
  }
}

/**
 * Choose the view shown when the page loads: the hash's, else the stored one, else the default.
 *
 * The filter text is never taken from storage, so a later visit does not start with guides hidden.
 *
 * @param {string} hash The URL hash, with or without the `#`.
 * @param {Storage | null} storage `localStorage`, or null when the browser does not give it.
 * @returns {{view: object, rewriteHash: boolean}} The view, and whether it came from storage, in which case the hash
 *     should be written to show it.
 */
export function initialView(hash, storage) {
  if (namesAny(hash, Object.keys(DEFAULT_VIEW))) {
    return { view: parseView(hash), rewriteHash: false };
  }
  const stored = readStored(storage);
  if (typeof stored === "string" && namesAny(stored, Object.keys(CHOICES))) {
    return { view: { ...parseView(stored), q: "" }, rewriteHash: true };
  }
  return { view: { ...DEFAULT_VIEW }, rewriteHash: false };
}

/**
 * Remember a view's sort, order and grouping; for the default view, forget any stored one.
 *
 * Removing the key for the default lets a later build change the default without an old stored value hiding it.
 *
 * @param {Storage | null} storage `localStorage`, or null when the browser does not give it; null does nothing.
 * @param {object} view The view; its filter text is not stored.
 */
export function saveView(storage, view) {
  if (storage === null) {
    return;
  }
  const kept = { ...view, q: "" };
  try {
    if (Object.keys(CHOICES).every((name) => kept[name] === DEFAULT_VIEW[name])) {
      storage.removeItem(STORAGE_KEY);
    } else {
      storage.setItem(STORAGE_KEY, formatView(kept));
    }
  } catch (error) {
    // Such as blocked site data or a full quota. The index works for this visit; only the next one starts afresh.
    console.warn("The view could not be remembered.", error);
  }
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

// The code below touches the page. The controls have the IDs of the view's settings (`sort`, `order`, `group`, `q`).
// Text from the payload goes in with `textContent` only, never `innerHTML`: titles and channel names come from YouTube.

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

function groupDetails(group, view, openGroups) {
  const summary = element("summary");
  summary.append(element("h2", null, `${group.label} · ${group.guides.length}`));
  const details = element("details", "group");
  details.dataset.key = groupKey(view, group.label);
  // Opened for the filter, not by the reader; the `toggle` listener in `main` leaves such groups out of `openGroups`.
  if (isFiltering(view)) {
    details.dataset.filtered = "";
  }
  details.open = isGroupOpen(view, group.label, openGroups);
  details.append(summary, guideList(group.guides, view));
  return details;
}

function listing(guides, shown, view, openGroups) {
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
    return groupDetails(group, view, openGroups);
  });
}

function fillChoices(select, choices) {
  const options = Object.entries(choices).map(([value, label]) => {
    const option = element("option", null, label);
    option.value = value;
    return option;
  });
  select.replaceChildren(...options);
}

function setControls(view) {
  for (const name of Object.keys(CHOICES)) {
    document.getElementById(name).value = view[name];
  }
  document.getElementById("q").value = view.q;
}

function readControls() {
  const view = { q: document.getElementById("q").value };
  for (const name of Object.keys(CHOICES)) {
    view[name] = document.getElementById(name).value;
  }
  return view;
}

function render(guides, view, openGroups) {
  const shown = sortGuides(filterGuides(guides, view.q), view);
  // Built before the page is changed, so a view that fails leaves the count line as it was.
  const items = listing(guides, shown, view, openGroups);
  document.getElementById("count").textContent = describeView(view, shown.length, guides.length);
  document.getElementById("listing").replaceChildren(...items);
}

function writeHash(view) {
  // `replaceState` does not fire `hashchange`, and keeps typing in the filter out of the back button's history.
  try {
    history.replaceState(null, "", `#${formatView(view)}`);
  } catch (error) {
    // Safari and Firefox are said to limit how often the History API may be called, and to throw past the limit (not
    // yet verified). The list is already rendered; only the URL lags until the next change.
    console.warn("The view could not be written to the URL.", error);
  }
}

function showFailure(error) {
  // Such as a browser without `Map.groupBy`. Say so instead of leaving a wrong or empty list, and hide the controls,
  // which no longer work; the console keeps the error.
  const note = element("p", "empty", "The list of guides could not be shown; the browser console has the error.");
  document.getElementById("listing").replaceChildren(note);
  document.getElementById("controls").hidden = true;
  throw error;
}

// Every render is guarded, not only the first: a view can fail where the default one works. Only the channel and
// month groupings use `Map.groupBy`, for example.
function guarded(listener) {
  return () => {
    try {
      listener();
    } catch (error) {
      showFailure(error);
    }
  };
}

function browserStorage() {
  // Reading the property itself can throw, such as when the browser blocks site data.
  try {
    return window.localStorage ?? null;
  } catch (error) {
    console.warn("The view will not be remembered: the browser gives no storage.", error);
    return null;
  }
}

function main() {
  let guides;
  let storage = null;
  // Whether the first view came from storage.
  let restored = false;
  // The groups the reader opened, kept for this visit only, so a render (a new sort, or typing in the filter) does not
  // close them again.
  const openGroups = new Set();
  // Every view shown is remembered, wherever it comes from, since it is the view on screen.
  const show = (view) => {
    setControls(view);
    render(guides, view, openGroups);
    saveView(storage, view);
  };
  const showHash = () => show(parseView(location.hash));
  const showControls = () => {
    const view = readControls();
    render(guides, view, openGroups);
    writeHash(view);
    saveView(storage, view);
  };
  const resetView = () => {
    openGroups.clear();
    show(DEFAULT_VIEW);
    writeHash(DEFAULT_VIEW);
  };
  try {
    guides = JSON.parse(document.getElementById("guides").textContent);
    for (const [name, choices] of Object.entries(CHOICES)) {
      fillChoices(document.getElementById(name), choices);
    }
    storage = browserStorage();
    const { view, rewriteHash } = initialView(location.hash, storage);
    restored = rewriteHash;
    show(view);
    if (rewriteHash) {
      writeHash(view);
    }
  } catch (error) {
    if (restored) {
      // A stored view that fails would fail on every visit without a hash, with "Reset view" hidden along with the
      // other controls. Forget it, so the next visit starts from the default. A failing hash keeps the stored view.
      saveView(storage, DEFAULT_VIEW);
    }
    showFailure(error);
  }
  // The hash edited by hand, or back and forward between hashes.
  window.addEventListener("hashchange", guarded(showHash));
  // `toggle` does not bubble, so it is caught on the way down. It also fires for a group created open, after the
  // render; one opened for the filter is skipped, or clearing the filter would leave every group open.
  const rememberToggle = (event) => {
    const details = event.target;
    if (!(details instanceof HTMLDetailsElement) || details.dataset.filtered !== undefined) {
      return;
    }
    if (details.open) {
      openGroups.add(details.dataset.key);
    } else {
      openGroups.delete(details.dataset.key);
    }
  };
  document.getElementById("listing").addEventListener("toggle", rememberToggle, true);
  const controls = document.getElementById("controls");
  // `input` bubbles from the selects too.
  controls.addEventListener("input", guarded(showControls));
  document.getElementById("reset").addEventListener("click", guarded(resetView));
  controls.hidden = false;
}

if (typeof document !== "undefined") {
  main();
}
