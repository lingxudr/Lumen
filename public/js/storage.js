/** LocalStorage — lanjut baca, preferensi, bookmark, riwayat */

const KEYS = {
  lastRead: "lumen:lastRead",
  prefs: "lumen:readerPrefs",
  bookmarks: "lumen:bookmarks",
  history: "lumen:history",
  progress: "lumen:readingProgress",
};

function readJSON(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}

function writeJSON(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* quota */
  }
}

/* —— Lanjut baca (single) —— */
export function getLastRead() {
  return readJSON(KEYS.lastRead, null);
}

export function saveLastRead(entry) {
  writeJSON(KEYS.lastRead, {
    slug: entry.slug,
    title: entry.title,
    chapter: entry.chapter,
    cover: entry.cover || "",
    at: Date.now(),
  });
  // juga masuk riwayat
  pushHistory(entry);
}

/* —— Preferensi reader —— */
export function getPrefs() {
  return {
    theme: "dark",
    fit: "width",
    chapterOrder: "desc", // desc = terbaru dulu, asc = dari ch 1
    mode: "webtoon", // webtoon | vertical | single | double
    autoNext: true,
    showProgress: true,
    tapNav: true,
    fullscreen: false,
    mode: "webtoon",
    ...readJSON(KEYS.prefs, {}),
  };
}

export function savePrefs(partial) {
  const next = { ...getPrefs(), ...partial };
  writeJSON(KEYS.prefs, next);
  return next;
}

/* —— Bookmark —— */
export function getBookmarks() {
  const list = readJSON(KEYS.bookmarks, []);
  return Array.isArray(list) ? list : [];
}

export function isBookmarked(slug) {
  return getBookmarks().some((b) => b.slug === slug);
}

export function toggleBookmark(entry) {
  const list = getBookmarks();
  const i = list.findIndex((b) => b.slug === entry.slug);
  if (i >= 0) {
    list.splice(i, 1);
    writeJSON(KEYS.bookmarks, list);
    return false;
  }
  list.unshift({
    slug: entry.slug,
    title: entry.title,
    cover: entry.cover || "",
    status: entry.status || "",
    format: entry.format || "",
    at: Date.now(),
  });
  writeJSON(KEYS.bookmarks, list.slice(0, 200));
  return true;
}

/* —— Riwayat baca —— */
export function getHistory() {
  const list = readJSON(KEYS.history, []);
  return Array.isArray(list) ? list : [];
}

export function pushHistory(entry) {
  if (!entry?.slug) return;
  let list = getHistory().filter((h) => h.slug !== entry.slug);
  list.unshift({
    slug: entry.slug,
    title: entry.title,
    chapter: entry.chapter,
    cover: entry.cover || "",
    at: Date.now(),
  });
  writeJSON(KEYS.history, list.slice(0, 100));
}

export function clearHistory() {
  writeJSON(KEYS.history, []);
}


/* —— Progress halaman per (slug, chapter) —— */
export function getReadingProgress(slug, chapter) {
  const all = readJSON(KEYS.progress, {});
  const key = `${slug}::${chapter}`;
  return all[key] || null;
}

export function saveReadingProgress(slug, chapter, page, total) {
  if (!slug || chapter == null) return;
  const all = readJSON(KEYS.progress, {});
  const key = `${slug}::${chapter}`;
  all[key] = { page: page || 0, total: total || 0, at: Date.now() };
  // cap keys
  const keys = Object.keys(all).sort((a, b) => (all[b].at || 0) - (all[a].at || 0));
  const trimmed = {};
  keys.slice(0, 300).forEach((k) => {
    trimmed[k] = all[k];
  });
  writeJSON(KEYS.progress, trimmed);
}
