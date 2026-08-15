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

export function parseChapterNumber(value) {
  if (value == null || value === "") return null;
  if (typeof value === "number" && Number.isFinite(value)) {
    return Number.isInteger(value) ? value : value;
  }
  const s = String(value).trim();
  if (!s) return null;
  const direct = Number(s);
  if (Number.isFinite(direct) && s === String(direct)) return direct;
  let m = s.match(/(?:chapter|ch\.?|ep\.?|episode)\s*(\d+(?:\.\d+)?)/i);
  if (m) return Number(m[1]);
  m = s.match(/\b(\d+)\s*[-–]\s*(\d+)\b/);
  if (m) return Number(m[1]) + Number(m[2]) / 10;
  m = s.match(/\b(\d+(?:\.\d+)?)\s*(?:part|pt\.?)\s*(\d+)/i);
  if (m) return Number(m[1]) + Number(m[2]) / 100;
  m = s.match(/(\d+(?:\.\d+)?)/);
  if (m) return Number(m[1]);
  return null;
}

export function chapterIndex(ch) {
  if (!ch) return null;
  const candidates = [
    ch.chapterIndex,
    ch.data && ch.data.index,
    ch.index,
    ch.chapter_number,
    ch.data && ch.data.chapter_number,
    ch.data && ch.data.title,
    ch.title,
    ch.name,
  ];
  for (const c of candidates) {
    const n = parseChapterNumber(c);
    if (n != null) return n;
  }
  return null;
}
