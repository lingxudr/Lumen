/**
 * Lumen — entry point
 */
import { showView, setOffline, toast } from "./ui.js";
import { createHomeView } from "./views/home.js";
import { createSeriesView } from "./views/series.js";
import { createReaderView } from "./views/reader.js";
import { createLibraryView } from "./views/library.js";

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
ctx.openSeries = series.openSeries;
ctx.openChapter = reader.openChapter;

const App = {
  go(where) {
    if (where === "home") {
      state.series = null;
      state.chapterData = null;
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
  openSeries: series.openSeries,
  openChapter: reader.openChapter,
  backToSeries() {
    if (state.series) showView("series");
    else App.go("home");
  },
  navChapter: reader.navChapter,
  reloadChapter: reader.reload,
  checkHotlink: reader.checkHotlink,
  setReaderTheme: reader.setTheme,
  setReaderFit: reader.setFit,
  library(mode) {
    document.querySelectorAll(".tab").forEach((t) => {
      t.classList.toggle("is-active", t.dataset.tab === mode);
    });
    library.open(mode);
  },
  clearHistory: library.onClearHistory,
  toggleReaderMenu() {
    const m = document.getElementById("reader-menu");
    if (m) m.classList.toggle("is-hidden");
  },
  refresh() {
    home.loadList();
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

  home.loadList();
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }
});
