/**
 * Lumen — entry point
 */
import { showView, setOffline, toast } from "./ui.js";
import { createHomeView } from "./views/home.js";
import { createSeriesView } from "./views/series.js";
import { createReaderView } from "./views/reader.js";
import { createLibraryView } from "./views/library.js";
import { parseLocation, navigate, pathFor } from "./router.js";
import { setMeta, setJsonLd, clearJsonLd, websiteJsonLd, breadcrumbJsonLd, chapterJsonLd, setJsonLdGraph } from "./seo.js";
import { initTheme, setUiTheme, applyTheme } from "./theme.js";
import {
  initEyeCareClinical,
  setRestReminder as eyeSetRest,
  setAutoEvening as eyeSetAuto,
  markEyeCareManual,
  scheduleRestReminder,
} from "./eye-care.js";

const state = {
  tab: "newest",
  page: 1,
  lastPage: 1,
  query: "",
  status: "",
  format: "",
  genre: "",
  series: null,
  chapters: [],
  chapterIndex: null,
  chapterData: null,
};

const ctx = { state, openSeries: null, openChapter: null };
const home = createHomeView(ctx);
const series = createSeriesView(ctx);
const reader = createReaderView(ctx);
const library = createLibraryView(ctx);
ctx.openSeries = async (slug, hint) => {
  navigate({ name: "series", slug });
  setMeta({
    title: `Lumen — ${hint?.title || slug}`,
    description: `Baca ${hint?.title || slug} online di Lumen`,
    url: location.origin + pathFor({ name: "series", slug }),
    type: "article",
  });
  return series.openSeries(slug, hint);
};
ctx.openChapter = async (index) => {
  const slug = state.series?.data?.slug || state.series?.slug || "";
  if (slug) {
    navigate({ name: "reader", slug, chapter: index });
    setMeta({
      title: `Lumen — ${state.series?.data?.title || slug} Ch.${index}`,
      description: `Baca chapter ${index}`,
      image: state.series?.data?.coverImage || "",
      url: location.origin + pathFor({ name: "reader", slug, chapter: index }),
      type: "article",
    });
  }
  return reader.openChapter(index);
};

const App = {
  go(where) {
    if (where === "home") {
      state.series = null;
      state.chapterData = null;
      state.query = "";
      state.tab = "newest";
      state.page = 1;
      state.genre = "";
      state.status = "";
      state.format = "";
      navigate({ name: "home", tab: "newest" }, { replace: false });
      document.querySelectorAll(".tab").forEach((t) => {
        t.classList.toggle("is-active", t.dataset.tab === "newest");
      });
      document.querySelectorAll("[data-nav]").forEach((el) => {
        el.classList.toggle("is-active", el.getAttribute("data-nav") === "newest");
      });
      showView("home");
      home.loadList({ force: true });
    }
  },
  tab(name) {
    home.setTab(name);
  },
  page: home.page,
  search: home.search,
  setFilter: home.setFilter,
  openSeries: (...a) => ctx.openSeries(...a),
  openChapter: (...a) => ctx.openChapter(...a),
  backToSeries() {
    if (state.series) showView("series");
    else App.go("home");
  },
  navChapter: (...a) => reader.navChapter(...a),
  reloadChapter: reader.reload,
  checkHotlink: reader.checkHotlink,
  setReaderTheme: reader.setTheme,
  setEyeCare(mode) {
    try { markEyeCareManual(); } catch (_) {}
    if (typeof reader.setEyeCare === "function") {
      reader.setEyeCare(mode);
    } else {
      const m = ["off", "warm", "night"].includes(mode) ? mode : "off";
      document.documentElement.setAttribute("data-eye-care", m);
      document.body.setAttribute("data-eye-care", m);
      try {
        const raw = localStorage.getItem("lumen:readerPrefs");
        const prefs = raw ? JSON.parse(raw) : {};
        prefs.eyeCare = m;
        localStorage.setItem("lumen:readerPrefs", JSON.stringify(prefs));
      } catch (_) {}
    }
    const labels = { off: "Normal", warm: "Hangat", night: "Malam" };
    try { toast("Perlindungan mata: " + (labels[mode] || mode)); } catch (_) {}
  },
  setRestReminder(on) {
    eyeSetRest(!!on);
    try { toast(on ? "Pengingat 20-20-20 aktif" : "Pengingat dimatikan"); } catch (_) {}
  },
  setAutoEveningEyeCare(on) {
    eyeSetAuto(!!on);
    if (on) {
      try { initEyeCareClinical((m) => App.setEyeCare(m)); } catch (_) {}
    }
    try { toast(on ? "Mode malam otomatis aktif" : "Mode malam otomatis off"); } catch (_) {}
  },

  setUiTheme(mode) {
    const r = setUiTheme(mode);
    const labels = { system: "Sistem", dark: "Gelap", amoled: "AMOLED", light: "Terang", sepia: "Sepia" };
    try { toast("Tema: " + (labels[mode] || mode)); } catch (_) {}
    return r;
  },
  openSettings() {
    const sheet = document.getElementById("settings-sheet");
    const bd = document.getElementById("settings-backdrop");
    if (!sheet) return;
    // sync controls
    try {
      const raw = localStorage.getItem("lumen:readerPrefs");
      const p = raw ? JSON.parse(raw) : {};
      const eye = p.eyeCare || document.documentElement.getAttribute("data-eye-care") || "off";
      document.querySelectorAll("#settings-eye-care [data-eye-care-btn]").forEach((b) => {
        b.classList.toggle("is-active", b.getAttribute("data-eye-care-btn") === eye);
      });
      const ut = p.uiTheme || "dark";
      document.querySelectorAll("[data-ui-theme-btn]").forEach((b) => {
        b.classList.toggle("is-active", b.getAttribute("data-ui-theme-btn") === ut);
      });
      const rr = document.getElementById("settings-rest-reminder");
      if (rr) rr.checked = p.restReminder !== false;
      const ae = document.getElementById("settings-auto-evening");
      if (ae) ae.checked = !!p.autoEveningEyeCare;
    } catch (_) {}
    sheet.classList.remove("is-hidden");
    if (bd) {
      bd.classList.remove("is-hidden");
      bd.setAttribute("aria-hidden", "false");
    }
    document.querySelectorAll(".bottom-link, .side-link").forEach((el) => {
      el.classList.toggle("is-active", el.getAttribute("data-nav") === "settings");
    });
  },
  closeSettings() {
    const sheet = document.getElementById("settings-sheet");
    const bd = document.getElementById("settings-backdrop");
    if (sheet) sheet.classList.add("is-hidden");
    if (bd) {
      bd.classList.add("is-hidden");
      bd.setAttribute("aria-hidden", "true");
    }
  },

  cycleEyeCare() {
    const order = ["off", "warm", "night"];
    const cur = document.documentElement.getAttribute("data-eye-care") || "off";
    const next = order[(Math.max(0, order.indexOf(cur)) + 1) % order.length];
    App.setEyeCare(next);
  },
  setReaderWidth: (v) => reader.setWidth?.(v),
  setReaderPref: (k, v) => reader.setPref?.(k, v),
  setReaderFit: reader.setFit,
  setReaderMode: reader.setMode,
  library(mode) {
    document.querySelectorAll(".tab").forEach((t) => {
      t.classList.toggle("is-active", t.dataset.tab === mode);
    });
    library.open(mode);
  },
  clearHistory: library.onClearHistory,
  toggleReaderMenu() {
    const m = document.getElementById("reader-menu");
    const bd = document.getElementById("reader-settings-backdrop");
    if (!m) return;
    const open = m.classList.contains("is-hidden");
    m.classList.toggle("is-hidden", !open);
    if (bd) bd.classList.toggle("is-hidden", !open);
  },
  refresh(force) {
    home.loadList({ force: !!force });
  },
};

window.App = App;

// Wake Railway backend early (reduces perceived cold start)


function startPresenceHeartbeat() {
  try {
    let sid = sessionStorage.getItem("lumen_sid");
    if (!sid) {
      sid = "s" + Math.random().toString(36).slice(2) + Date.now().toString(36);
      sessionStorage.setItem("lumen_sid", sid);
    }
    const beat = () => {
      try {
        const body = {
          session: sid,
          path: location.pathname + location.search + location.hash,
        };
        const url = (window.LUMEN_API_BASE || "/api") + "/presence";
        fetch(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
          keepalive: true,
          credentials: "omit",
          cache: "no-store",
        }).catch(() => {});
      } catch (_) {}
    };
    beat();
    setInterval(beat, 45000);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") beat();
    });
  } catch (_) {}
}

function reportVisit() {
  try {
    const key = "lumen_visit_ping";
    if (sessionStorage.getItem(key)) return;
    sessionStorage.setItem(key, "1");
    let tz = "";
    try {
      tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "";
    } catch (_) {}
    const body = {
      client: "lumen-web",
      path: location.pathname + location.search + location.hash,
      referrer: document.referrer || "",
      ua: navigator.userAgent || "",
      lang: navigator.language || "",
      languages: Array.isArray(navigator.languages) ? navigator.languages.slice(0, 5) : [],
      screen: (screen.width || 0) + "x" + (screen.height || 0),
      tz,
      platform: navigator.platform || "",
      hw: navigator.hardwareConcurrency || 0,
      mem: navigator.deviceMemory || 0,
      touch: navigator.maxTouchPoints || 0,
      webdriver: !!(navigator.webdriver),
      cookie: navigator.cookieEnabled !== false,
      dnt: navigator.doNotTrack || "",
    };
    const url = (window.LUMEN_API_BASE || "/api") + "/visit";
    const blob = new Blob([JSON.stringify(body)], { type: "application/json" });
    if (navigator.sendBeacon) {
      navigator.sendBeacon(url, blob);
    } else {
      fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        keepalive: true,
        credentials: "omit",
      }).catch(() => {});
    }
  } catch (_) {}
}

function wakeBackend() {
  const base = window.LUMEN_API_BASE || "/api";
  const ping = (path) => {
    try {
      const ctrl = new AbortController();
      const t = setTimeout(() => ctrl.abort(), 12000);
      fetch(base + path, {
        signal: ctrl.signal,
        cache: "no-store",
        credentials: "omit",
      })
        .catch(() => {})
        .finally(() => clearTimeout(t));
    } catch (_) {}
  };
  // Multi-hit: ping + health + warm newest list (anti cold-start Railway)
  ping("/ping");
  ping("/health");
  ping("/series?take=12&page=1&mode=newest&takeChapter=2");
}

let _wakeTimer = null;
function scheduleWakeKeepalive() {
  wakeBackend();
  if (_wakeTimer) clearInterval(_wakeTimer);
  // Keep Railway warm ~every 4 minutes while tab open
  _wakeTimer = setInterval(wakeBackend, 4 * 60 * 1000);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") wakeBackend();
  });
}

scheduleWakeKeepalive();

function applyEyeCareFromPrefs() {
  try {
    const raw = localStorage.getItem("lumen:readerPrefs");
    const prefs = raw ? JSON.parse(raw) : {};
    const eye = prefs.eyeCare || "off";
    document.documentElement.setAttribute("data-eye-care", eye);
    document.body.setAttribute("data-eye-care", eye);
  } catch (_) {
    document.documentElement.setAttribute("data-eye-care", "off");
  }
}
try { initTheme(); } catch (e) { console.warn("theme", e); }
applyEyeCareFromPrefs();

/** Auto hard-refresh when deploy ships new version.json (bypass SW/cache). */
async function checkAssetVersion() {
  try {
    if (sessionStorage.getItem("lumen:reloadLock")) return;
    const res = await fetch("/version.json?_=" + Date.now(), { cache: "no-store" });
    if (!res.ok) return;
    const data = await res.json();
    const v = String(data.v || data.version || "");
    if (!v) return;
    const key = "lumen:assetVersion";
    const prev = localStorage.getItem(key);
    if (!prev) {
      localStorage.setItem(key, v);
      return;
    }
    if (prev === v) return;
    localStorage.setItem(key, v);
    const flag = "lumen:reloaded:" + v;
    if (sessionStorage.getItem(flag)) return;
    sessionStorage.setItem(flag, "1");
    sessionStorage.setItem("lumen:reloadLock", "1");
    try {
      if ("serviceWorker" in navigator) {
        const regs = await navigator.serviceWorker.getRegistrations();
        await Promise.all(regs.map((r) => r.unregister().catch(() => {})));
      }
      if (window.caches) {
        const keys = await caches.keys();
        await Promise.all(keys.map((k) => caches.delete(k)));
      }
    } catch (_) {}
    location.reload();
  } catch (_) {}
}
checkAssetVersion();
setInterval(checkAssetVersion, 5 * 60 * 1000);


try {
  initEyeCareClinical((m) => {
    // auto evening should not mark as manual
    if (typeof reader.setEyeCare === "function") reader.setEyeCare(m);
  });
} catch (e) { console.warn("eye-care init", e); }

reportVisit();
startPresenceHeartbeat();

document.addEventListener("DOMContentLoaded", () => {
  const closeS = () => App.closeSettings();
  document.getElementById("btn-close-settings")?.addEventListener("click", closeS);
  document.getElementById("settings-backdrop")?.addEventListener("click", closeS);
  try {
    const raw = localStorage.getItem("lumen:readerPrefs");
    const p = raw ? JSON.parse(raw) : {};
    const rr = document.getElementById("pref-rest-reminder");
    if (rr) rr.checked = p.restReminder !== false;
    const ae = document.getElementById("pref-auto-evening");
    if (ae) ae.checked = !!p.autoEveningEyeCare;
  } catch (_) {}
  setOffline(typeof navigator !== "undefined" && navigator.onLine === false);
  window.addEventListener("offline", () => {
    setOffline(true);
    toast("Koneksi terputus");
  });
  window.addEventListener("online", () => {
    setOffline(false);
    toast("Koneksi kembali");
    home.loadList();
  });

  window.addEventListener("popstate", () => {
    routeFromLocation();
  });

  routeFromLocation();
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }
});

async function routeFromLocation() {
  const r = parseLocation();
  if (r.name === "series" && r.slug) {
    await ctx.openSeries(r.slug);
    return;
  }
  if (r.name === "reader" && r.slug) {
    await series.openSeries(r.slug);
    await ctx.openChapter(r.chapter);
    return;
  }
  // Sync search from URL (refresh / back). Empty q clears stuck search UI.
  state.query = r.query || "";
  const qEl = document.getElementById("q");
  if (qEl) qEl.value = state.query;
  if (r.tab) state.tab = r.tab;
  if (state.query) {
    const titleEl = document.getElementById("list-title");
    if (titleEl) titleEl.textContent = `Hasil untuk "${state.query}"`;
  } else {
    const titles = {
      newest: "Terbaru",
      new_series: "Series Baru",
      completed: "Selesai",
      browse: "Browse",
      hot: "Populer",
    };
    const titleEl = document.getElementById("list-title");
    if (titleEl) titleEl.textContent = titles[state.tab] || "Terbaru";
  }
  const homePath = state.query
    ? `/search?q=${encodeURIComponent(state.query)}`
    : r.tab === "hot"
      ? "/popular"
      : r.tab && r.tab !== "newest"
        ? `/latest?tab=${encodeURIComponent(r.tab)}`
        : "/";
  setMeta({
    title: state.query
      ? `Cari "${state.query}" — Lumen`
      : "Lumen — Baca Komik Online",
    description:
      "Baca manga, manhwa, dan manhua online gratis. Update chapter terbaru setiap hari di Lumen.",
    url: homePath,
    keywords: "manga, manhwa, manhua, komik online, baca komik gratis",
  });
  try {
    setJsonLdGraph([
      websiteJsonLd(),
      breadcrumbJsonLd([{ name: "Beranda", path: "/" }]),
    ]);
  } catch (_) {
    clearJsonLd();
  }
  home.loadList();
}
