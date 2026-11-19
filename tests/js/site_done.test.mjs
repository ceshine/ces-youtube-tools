// Tests for the done marks' pure functions. The page code (`showToggle`, `initGuidePage`) needs a page and is checked
// by hand, as the index's page code is.
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  DONE_KEY,
  EXPORT_FORMAT,
  EXPORT_VERSION,
  parseDone,
  formatDone,
  formatDoneExport,
  parseDoneExport,
  readDone,
  setDone,
  mergeDone,
} from "../../src/ces_youtube_tools/templates/site_done.mjs";

// A stand-in for `localStorage`; `items` shows what was stored.
function memoryStorage(entries = {}) {
  const items = new Map(Object.entries(entries));
  return {
    items,
    getItem: (key) => (items.has(key) ? items.get(key) : null),
    setItem: (key, value) => items.set(key, String(value)),
    removeItem: (key) => items.delete(key),
  };
}

function brokenStorage() {
  const fail = () => {
    throw new Error("storage is blocked");
  };
  return { getItem: fail, setItem: fail, removeItem: fail };
}

// Reads work, writes fail, as with a full quota.
function fullStorage(entries = {}) {
  const storage = memoryStorage(entries);
  const fail = () => {
    throw new Error("the quota is full");
  };
  return { ...storage, setItem: fail, removeItem: fail };
}

test("parseDone reads no value as no marks", () => {
  assert.deepEqual(parseDone(null), new Set());
});

test("parseDone reads a list of video IDs", () => {
  assert.deepEqual(parseDone('["bbbbbbbbbbb","aaaaaaaaaaa"]'), new Set(["aaaaaaaaaaa", "bbbbbbbbbbb"]));
  assert.deepEqual(parseDone("[]"), new Set());
});

test("parseDone rejects anything but a list of strings", () => {
  for (const text of ["not json", '{"aaaaaaaaaaa": true}', '["aaaaaaaaaaa", 1]', '"aaaaaaaaaaa"', "null"]) {
    assert.throws(() => parseDone(text), undefined, text);
  }
});

test("formatDone sorts, so the same marks give the same value", () => {
  assert.equal(formatDone(new Set(["bbbbbbbbbbb", "aaaaaaaaaaa"])), '["aaaaaaaaaaa","bbbbbbbbbbb"]');
  assert.deepEqual(parseDone(formatDone(new Set(["aaaaaaaaaaa"]))), new Set(["aaaaaaaaaaa"]));
});

test("readDone gives null without storage", () => {
  assert.equal(readDone(null), null);
});

test("readDone gives null when storage cannot be read", (t) => {
  const warn = t.mock.method(console, "warn", () => {});
  assert.equal(readDone(brokenStorage()), null);
  assert.equal(warn.mock.callCount(), 1);
});

test("readDone reads the stored marks", () => {
  assert.deepEqual(readDone(memoryStorage()), new Set());
  assert.deepEqual(readDone(memoryStorage({ [DONE_KEY]: '["aaaaaaaaaaa"]' })), new Set(["aaaaaaaaaaa"]));
});

test("readDone takes an unreadable value as no marks and leaves it stored", (t) => {
  const warn = t.mock.method(console, "warn", () => {});
  const storage = memoryStorage({ [DONE_KEY]: "{oops" });
  assert.deepEqual(readDone(storage), new Set());
  assert.equal(warn.mock.callCount(), 1);
  assert.equal(storage.items.get(DONE_KEY), "{oops");
});

test("setDone adds and removes marks, and removes the key with the last one", () => {
  const storage = memoryStorage();
  assert.equal(setDone(storage, "bbbbbbbbbbb", true), true);
  assert.equal(setDone(storage, "aaaaaaaaaaa", true), true);
  assert.equal(storage.items.get(DONE_KEY), '["aaaaaaaaaaa","bbbbbbbbbbb"]');
  assert.equal(setDone(storage, "aaaaaaaaaaa", true), true);
  assert.equal(storage.items.get(DONE_KEY), '["aaaaaaaaaaa","bbbbbbbbbbb"]');
  assert.equal(setDone(storage, "bbbbbbbbbbb", false), true);
  assert.equal(storage.items.get(DONE_KEY), '["aaaaaaaaaaa"]');
  assert.equal(setDone(storage, "aaaaaaaaaaa", false), true);
  assert.equal(storage.items.has(DONE_KEY), false);
});

test("setDone clearing a mark that is not there stores nothing", () => {
  const storage = memoryStorage();
  assert.equal(setDone(storage, "aaaaaaaaaaa", false), true);
  assert.equal(storage.items.has(DONE_KEY), false);
});

test("setDone keeps a mark another tab stored after this page read the marks", () => {
  const storage = memoryStorage();
  const seen = readDone(storage);
  storage.setItem(DONE_KEY, '["ccccccccccc"]');
  assert.equal(seen.has("ccccccccccc"), false);
  assert.equal(setDone(storage, "aaaaaaaaaaa", true), true);
  assert.equal(storage.items.get(DONE_KEY), '["aaaaaaaaaaa","ccccccccccc"]');
});

test("setDone replaces an unreadable value", (t) => {
  t.mock.method(console, "warn", () => {});
  const storage = memoryStorage({ [DONE_KEY]: "{oops" });
  assert.equal(setDone(storage, "aaaaaaaaaaa", true), true);
  assert.equal(storage.items.get(DONE_KEY), '["aaaaaaaaaaa"]');
});

test("setDone without storage does nothing", () => {
  assert.equal(setDone(null, "aaaaaaaaaaa", true), false);
});

test("setDone fails when storage cannot be read", (t) => {
  const warn = t.mock.method(console, "warn", () => {});
  assert.equal(setDone(brokenStorage(), "aaaaaaaaaaa", true), false);
  assert.equal(warn.mock.callCount(), 1);
});

test("setDone fails when storage cannot be written, and leaves it as it was", (t) => {
  const warn = t.mock.method(console, "warn", () => {});
  const storage = fullStorage({ [DONE_KEY]: '["aaaaaaaaaaa"]' });
  assert.equal(setDone(storage, "bbbbbbbbbbb", true), false);
  assert.equal(setDone(storage, "aaaaaaaaaaa", false), false);
  assert.equal(warn.mock.callCount(), 2);
  assert.equal(storage.items.get(DONE_KEY), '["aaaaaaaaaaa"]');
});

test("formatDoneExport writes sorted versioned JSON and round-trips through parseDoneExport", () => {
  const text = formatDoneExport(new Set(["bbbbbbbbbbb", "aaaaaaaaaaa"]));
  assert.equal(
    text,
    `{\n  "format": "${EXPORT_FORMAT}",\n  "version": ${EXPORT_VERSION},\n  "done": [\n    "aaaaaaaaaaa",\n    "bbbbbbbbbbb"\n  ]\n}\n`,
  );
  assert.deepEqual(parseDoneExport(text), new Set(["aaaaaaaaaaa", "bbbbbbbbbbb"]));
  assert.deepEqual(parseDoneExport(formatDoneExport(new Set())), new Set());
});

test("parseDoneExport deduplicates video IDs", () => {
  const text = JSON.stringify({
    format: EXPORT_FORMAT,
    version: EXPORT_VERSION,
    done: ["aaaaaaaaaaa", "bbbbbbbbbbb", "aaaaaaaaaaa"],
  });
  assert.deepEqual(parseDoneExport(text), new Set(["aaaaaaaaaaa", "bbbbbbbbbbb"]));
});

test("parseDoneExport rejects invalid JSON or wrong schema", () => {
  const badInputs = [
    ["not json", /not valid JSON/],
    ["null", /not a done list export/],
    ['["aaaaaaaaaaa"]', /not a done list export/],
    [JSON.stringify({ format: "other", version: 1, done: [] }), /not a ces-youtube-tools done list export/],
    [JSON.stringify({ format: EXPORT_FORMAT, version: 2, done: [] }), /Unsupported done list export version: 2/],
    [JSON.stringify({ format: EXPORT_FORMAT, version: 1, done: "aaaaaaaaaaa" }), /must be an array/],
    [JSON.stringify({ format: EXPORT_FORMAT, version: 1, done: ["aaaaaaaaaaa", 1] }), /must be an array/],
    [JSON.stringify({ format: EXPORT_FORMAT, version: 1, done: ["aaaaaaaaaaa", ""] }), /must be an array/],
  ];
  for (const [text, pattern] of badInputs) {
    assert.throws(() => parseDoneExport(text), pattern, text);
  }
});

test("mergeDone unions imported IDs with stored marks and reports counts", () => {
  const storage = memoryStorage({ [DONE_KEY]: '["aaaaaaaaaaa"]' });
  assert.deepEqual(mergeDone(storage, new Set(["aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc"])), {
    ok: true,
    added: 2,
    total: 3,
  });
  assert.equal(storage.items.get(DONE_KEY), '["aaaaaaaaaaa","bbbbbbbbbbb","ccccccccccc"]');
  // Re-importing the same IDs adds 0.
  assert.deepEqual(mergeDone(storage, new Set(["bbbbbbbbbbb"])), { ok: true, added: 0, total: 3 });
});

test("mergeDone with no new IDs leaves storage untouched", (t) => {
  const empty = memoryStorage();
  assert.deepEqual(mergeDone(empty, new Set()), { ok: true, added: 0, total: 0 });
  assert.equal(empty.items.has(DONE_KEY), false);

  const existing = memoryStorage({ [DONE_KEY]: '["aaaaaaaaaaa"]' });
  assert.deepEqual(mergeDone(existing, new Set()), { ok: true, added: 0, total: 1 });
  assert.equal(existing.items.get(DONE_KEY), '["aaaaaaaaaaa"]');

  // Full-quota storage still succeeds when nothing needs to be written.
  const full = fullStorage({ [DONE_KEY]: '["aaaaaaaaaaa"]' });
  assert.deepEqual(mergeDone(full, new Set(["aaaaaaaaaaa"])), { ok: true, added: 0, total: 1 });

  // An empty import leaves an unreadable stored value intact until a real mark replaces it.
  t.mock.method(console, "warn", () => {});
  const corrupt = memoryStorage({ [DONE_KEY]: "{oops" });
  assert.deepEqual(mergeDone(corrupt, new Set()), { ok: true, added: 0, total: 0 });
  assert.equal(corrupt.items.get(DONE_KEY), "{oops");
});

test("mergeDone keeps marks written by another tab and replaces unreadable storage", (t) => {
  const storage = memoryStorage();
  const seen = readDone(storage);
  storage.setItem(DONE_KEY, '["ccccccccccc"]');
  assert.equal(seen.has("ccccccccccc"), false);
  assert.deepEqual(mergeDone(storage, new Set(["aaaaaaaaaaa"])), { ok: true, added: 1, total: 2 });
  assert.equal(storage.items.get(DONE_KEY), '["aaaaaaaaaaa","ccccccccccc"]');

  t.mock.method(console, "warn", () => {});
  const corrupt = memoryStorage({ [DONE_KEY]: "{oops" });
  assert.deepEqual(mergeDone(corrupt, new Set(["aaaaaaaaaaa"])), { ok: true, added: 1, total: 1 });
  assert.equal(corrupt.items.get(DONE_KEY), '["aaaaaaaaaaa"]');
});

test("mergeDone fails cleanly without storage or when storage throws", (t) => {
  assert.deepEqual(mergeDone(null, new Set(["aaaaaaaaaaa"])), { ok: false, added: 0, total: 0 });

  const warn = t.mock.method(console, "warn", () => {});
  assert.deepEqual(mergeDone(brokenStorage(), new Set(["aaaaaaaaaaa"])), { ok: false, added: 0, total: 0 });

  const full = fullStorage({ [DONE_KEY]: '["aaaaaaaaaaa"]' });
  assert.deepEqual(mergeDone(full, new Set(["bbbbbbbbbbb"])), { ok: false, added: 0, total: 0 });
  assert.equal(full.items.get(DONE_KEY), '["aaaaaaaaaaa"]');
  assert.equal(warn.mock.callCount(), 2);
});
