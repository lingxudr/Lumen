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
  navChapter: reader.navChapter,
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
  if (r.query) {
    state.query = r.query;
    const q = document.getElementById("q");
    if (q) q.value = r.query;
  }
  if (r.tab) state.tab = r.tab;
  setMeta({
    title: "Lumen — Baca Komik Online",
    description: "Baca komik online dengan koleksi terbaru setiap hari",
    url: location.origin + (r.tab === "hot" ? "/popular" : "/latest"),
  });
  clearJsonLd();
  home.loadList();
}
