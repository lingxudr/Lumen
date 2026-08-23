/**
 * Lumen — entry point
 */
import { showView, setOffline, toast } from "./ui.js";
import { createHomeView } from "./views/home.js";
import { createSeriesView } from "./views/series.js";
import { createReaderView } from "./views/reader.js";
import { createLibraryView } from "./views/library.js";
import { parseLocation, navigate, pathFor } from "./router.js";
import { setMeta, setJsonLd, clearJsonLd } from "./seo.js";

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
      navigate({ name: "home", tab: state.tab }, { replace: false });
      document.querySelectorAll(".tab").forEach((t) => {
        t.classList.toggle("is-active", t.dataset.tab === state.tab);
      });
      document.querySelectorAll("[data-nav]").forEach((el) => {
        el.classList.toggle("is-active", el.getAttribute("data-nav") === state.tab);
      });
      showView("home");
      home.loadList();
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
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 8000);
    fetch((window.LUMEN_API_BASE || "/api") + "/ping", {
      signal: ctrl.signal,
      cache: "no-store",
      credentials: "omit",
    }).catch(() => {}).finally(() => clearTimeout(t));
  } catch (_) {}
}
wakeBackend();
reportVisit();
startPresenceHeartbeat();

document.addEventListener("DOMContentLoaded", () => {
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
  setMeta({
    title: state.query
      ? `Cari "${state.query}" — Lumen`
      : "Lumen — Baca Komik Online",
    description: "Baca komik online dengan koleksi terbaru setiap hari",
    url: location.origin + (state.query
      ? `/search?q=${encodeURIComponent(state.query)}`
      : r.tab === "hot"
        ? "/popular"
        : "/latest"),
  });
  clearJsonLd();
  home.loadList();
}
