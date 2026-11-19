// The done marks, shared by the index's script (`index.mjs`) and the guide pages, and written to the site under this
// same name, so `index.mjs` finds it with a relative import both in the site and next to `site_index.mjs` here. The
// marks are kept in this browser only, in `localStorage`, as a JSON array of video IDs.
//
// The pure functions are exported for the Node tests in `tests/js/`. The page code at the end runs only when called,
// so importing this file in Node has no side effects.

// The key `localStorage` keeps the marks under. The index keeps its view under `ces-youtube-tools:index-view`.
export const DONE_KEY = "ces-youtube-tools:done";
export const EXPORT_FORMAT = "ces-youtube-tools:done";
export const EXPORT_VERSION = 1;

/**
 * Read the marks from the stored value.
 *
 * @param {string | null} text The value under `DONE_KEY`, or null when there is none.
 * @returns {Set<string>} The video IDs marked done; empty for null.
 * @throws {Error} If the text is not JSON, or not a JSON array of strings.
 */
export function parseDone(text) {
  if (text === null) {
    return new Set();
  }
  const value = JSON.parse(text);
  if (!Array.isArray(value) || !value.every((item) => typeof item === "string")) {
    throw new Error(`The done marks are not a list of video IDs: ${text}`);
  }
  return new Set(value);
}

/**
 * Write the marks as `parseDone` reads them.
 *
 * @param {Iterable<string>} done The video IDs marked done.
 * @returns {string} A JSON array, sorted, so the same marks always give the same value.
 */
export function formatDone(done) {
  return JSON.stringify([...done].toSorted());
}

/**
 * Read the marks from storage.
 *
 * @param {Storage | null} storage `localStorage`, or null when the browser does not give it.
 * @returns {Set<string> | null} The video IDs marked done; an empty set, with a warning, for a value `parseDone`
 *     rejects. Null when there is no storage or it cannot be read, in which case no toggle is shown.
 */
export function readDone(storage) {
  if (storage === null) {
    return null;
  }
  let text;
  try {
    text = storage.getItem(DONE_KEY);
  } catch (error) {
    console.warn("The done marks could not be read.", error);
    return null;
  }
  try {
    return parseDone(text);
  } catch (error) {
    // Left in storage, so nothing is lost until the next mark replaces it.
    console.warn("The done marks are unreadable and are taken as none.", error);
    return new Set();
  }
}

/**
 * Mark a guide done or not done.
 *
 * The marks are read again first, so a mark another tab wrote since this page read them is kept.
 *
 * @param {Storage | null} storage `localStorage`, or null when the browser does not give it; null does nothing.
 * @param {string} videoId The guide's video.
 * @param {boolean} done True to mark it done, false to clear the mark.
 * @returns {boolean} True if the mark was stored; false, with a warning, if storage could not be read or written,
 *     in which case it is unchanged.
 */
export function setDone(storage, videoId, done) {
  const marks = readDone(storage);
  if (marks === null) {
    return false;
  }
  if (done) {
    marks.add(videoId);
  } else {
    marks.delete(videoId);
  }
  try {
    // Removed when empty, as the index's stored view is for the default view.
    if (marks.size === 0) {
      storage.removeItem(DONE_KEY);
    } else {
      storage.setItem(DONE_KEY, formatDone(marks));
    }
  } catch (error) {
    // Such as blocked site data or a full quota.
    console.warn("The done mark could not be stored.", error);
    return false;
  }
  return true;
}

/**
 * Format the done marks as a versioned JSON export file.
 *
 * @param {Iterable<string>} done The video IDs marked done.
 * @returns {string} Pretty-printed JSON with sorted video IDs and a trailing newline.
 */
export function formatDoneExport(done) {
  return `${JSON.stringify({ format: EXPORT_FORMAT, version: EXPORT_VERSION, done: [...done].toSorted() }, null, 2)}\n`;
}

/**
 * Parse and validate a JSON export file created by `formatDoneExport`.
 *
 * @param {string} text The raw file contents.
 * @returns {Set<string>} The exported video IDs.
 * @throws {Error} If the text is not valid JSON or does not match the export schema.
 */
export function parseDoneExport(text) {
  let value;
  try {
    value = JSON.parse(text);
  } catch {
    throw new Error("The file is not valid JSON.");
  }
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("The file is not a done list export.");
  }
  if (value.format !== EXPORT_FORMAT) {
    throw new Error("The file is not a ces-youtube-tools done list export.");
  }
  if (value.version !== EXPORT_VERSION) {
    throw new Error(`Unsupported done list export version: ${value.version}.`);
  }
  if (!Array.isArray(value.done) || !value.done.every((item) => typeof item === "string" && item.length > 0)) {
    throw new Error("The export's done list must be an array of video IDs.");
  }
  return new Set(value.done);
}

/**
 * Merge imported video IDs into the stored done marks.
 *
 * The marks are read again first, so a mark another tab wrote since this page read them is kept.
 *
 * @param {Storage | null} storage `localStorage`, or null when the browser does not give it; null does nothing.
 * @param {Iterable<string>} imported The video IDs to add.
 * @returns {{ok: boolean, added: number, total: number}} Whether the merge was stored, how many new IDs were added,
 *     and the resulting total count.
 */
export function mergeDone(storage, imported) {
  const marks = readDone(storage);
  if (marks === null) {
    return { ok: false, added: 0, total: 0 };
  }
  const before = marks.size;
  for (const videoId of imported) {
    marks.add(videoId);
  }
  const added = marks.size - before;
  if (added === 0) {
    return { ok: true, added: 0, total: marks.size };
  }
  try {
    storage.setItem(DONE_KEY, formatDone(marks));
  } catch (error) {
    console.warn("The imported done marks could not be stored.", error);
    return { ok: false, added: 0, total: 0 };
  }
  return { ok: true, added, total: marks.size };
}

// The code below touches the page.

/**
 * Get `localStorage`, if the browser gives it.
 *
 * @returns {Storage | null} `localStorage`, or null, with a warning, when it is missing or blocked.
 */
export function browserStorage() {
  // Reading the property itself can throw, such as when the browser blocks site data.
  try {
    return window.localStorage ?? null;
  } catch (error) {
    console.warn("Nothing will be remembered: the browser gives no storage.", error);
    return null;
  }
}

/**
 * Show a toggle's state: `aria-pressed`, and `✓` or `○` in its `.done-mark`. The button's name is left as written.
 *
 * @param {HTMLElement} button The toggle.
 * @param {boolean} done Whether its guide is marked done.
 */
export function showToggle(button, done) {
  button.setAttribute("aria-pressed", String(done));
  button.querySelector(".done-mark").textContent = done ? "✓" : "○";
}

/**
 * Make the guide page's `.done-toggle` buttons work, and show them.
 *
 * The buttons carry the page's video ID in `data-video-id` and are kept in step: a click on either marks the guide
 * and updates both. The marks are read again when the page is shown from the back-forward cache and when another tab
 * changes them. Without storage, the buttons stay hidden.
 *
 * @param {Storage | null} storage `localStorage`, or null when the browser does not give it.
 */
export function initGuidePage(storage) {
  const buttons = [...document.querySelectorAll(".done-toggle")];
  if (buttons.length === 0 || readDone(storage) === null) {
    return;
  }
  const videoId = buttons[0].dataset.videoId;
  if (!videoId) {
    // Stored, a missing ID would be `null` in the array, which `parseDone` rejects, so every mark would be lost.
    console.warn("The done toggles have no video ID, so they stay hidden.");
    return;
  }
  const update = () => {
    const marks = readDone(storage);
    if (marks === null) {
      return;
    }
    for (const button of buttons) {
      showToggle(button, marks.has(videoId));
      button.hidden = false;
    }
  };
  for (const button of buttons) {
    button.addEventListener("click", () => {
      const done = button.getAttribute("aria-pressed") !== "true";
      if (setDone(storage, videoId, done)) {
        for (const each of buttons) {
          showToggle(each, done);
        }
      }
    });
  }
  window.addEventListener("pageshow", update);
  // A null key is storage cleared.
  window.addEventListener("storage", (event) => {
    if (event.key === DONE_KEY || event.key === null) {
      update();
    }
  });
  update();
}
