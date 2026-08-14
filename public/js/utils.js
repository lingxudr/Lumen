export function $(sel, root = document) {
  return root.querySelector(sel);
}

export function $$(sel, root = document) {
  return Array.from(root.querySelectorAll(sel));
}

export function esc(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function escAttr(s) {
  return esc(s).replace(/'/g, "&#39;");
}

export function relTime(iso) {
  if (!iso) return "";
  try {
    const sec = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
    if (sec < 60) return `${Math.floor(sec)} dtk`;
    const m = sec / 60;
    if (m < 60) return `${Math.floor(m)} mnt`;
    const h = m / 60;
    if (h < 24) return `${Math.floor(h)} jam`;
    const d = h / 24;
    if (d < 30) return `${Math.floor(d)} hari`;
    return `${Math.floor(d / 30)} bln`;
  } catch {
    return "";
  }
}

export function isNew(iso, hours = 24) {
  if (!iso) return false;
  return Date.now() - new Date(iso).getTime() <= hours * 3600 * 1000;
}

export function chapterIndex(ch) {
  if (!ch) return null;
  if (ch.chapterIndex != null) return ch.chapterIndex;
  if (ch.data && ch.data.index != null) return ch.data.index;
  if (ch.index != null) return ch.index;
  if (ch.chapter_number != null) return ch.chapter_number;
  if (ch.data && ch.data.chapter_number != null) return ch.data.chapter_number;
  return null;
}
