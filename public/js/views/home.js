import { Config } from "../config.js";
import { navigate } from "../router.js";
import { setMeta, clearJsonLd, setJsonLd, websiteJsonLd, breadcrumbJsonLd } from "../seo.js";
import { api, apiPeek, clearApiCache, friendlyError } from "../api.js";
import { $, esc, relTime, isNew, chapterIndex } from "../utils.js";
import { toast, loading, showView, setImg, renderState } from "../ui.js";
import { proxyImageUrl } from "../api.js";
import { getLastRead } from "../storage.js";


function syncNav(active) {
  document.querySelectorAll("[data-nav]").forEach((el) => {
    el.classList.toggle("is-active", el.getAttribute("data-nav") === active);
  });
  document.querySelectorAll(".tab[data-tab]").forEach((t) => {
    t.classList.toggle("is-active", t.dataset.tab === active);
  });
}

function renderHero(items) {
  const box = document.getElementById("home-hero");
  if (!box) return;
  const item = (items || []).find((it) => {
    const d = it && (it.data || it);
    return d && (d.coverImage || d.cover || d.backgroundImage);
  });
  if (!item) {
    box.classList.add("is-hidden");
    box.innerHTML = "";
    return;
  }
  const d = item.data || item;
  const slug = d.slug || item.slug || "";
  const cover = d.backgroundImage || d.coverImage || d.cover || "";
  const rating = d.rating != null && d.rating !== "" ? `★ ${d.rating}` : "";
  const meta = [rating, d.status, d.format || d.type].filter(Boolean).join(" · ");
  const syn = (d.synopsis || "").trim();
  box.classList.remove("is-hidden");
  box.innerHTML = `
    <div class="home-hero-bg"></div>
    <div class="home-hero-shade"></div>
    <div class="home-hero-body">
      <div class="home-hero-kicker">Featured</div>
      <h2 class="home-hero-title"></h2>
      <div class="home-hero-meta"></div>
      <p class="home-hero-syn"></p>
      <div class="home-hero-actions">
        <button type="button" class="btn btn-primary" id="hero-open">View Details</button>
      </div>
    </div>`;
  const bg = box.querySelector(".home-hero-bg");
  if (bg && cover) bg.style.backgroundImage = `url("${proxyImageUrl(cover, { webp: true, w: 900 })}")`;
  box.querySelector(".home-hero-title").textContent = d.title || slug;
  box.querySelector(".home-hero-meta").textContent = meta;
  const synEl = box.querySelector(".home-hero-syn");
  if (synEl) {
    synEl.textContent = syn || "";
    synEl.style.display = syn ? "" : "none";
  }
  const btn = box.querySelector("#hero-open");
  if (btn) btn.onclick = () => {
    if (typeof window.App !== "undefined" && App.openSeries) {
      App.openSeries(slug, { title: d.title, cover: d.coverImage || cover });
    }
  };
}

export function createHomeView(ctx) {
  let genresLoaded = false;

  const FALLBACK_GENRES = [
    { name: "Action", slug: "action" },
    { name: "Adventure", slug: "adventure" },
    { name: "Romance", slug: "romance" },
    { name: "Fantasy", slug: "fantasy" },
    { name: "Isekai", slug: "isekai" },
    { name: "Comedy", slug: "comedy" },
    { name: "Drama", slug: "drama" },
    { name: "Ecchi", slug: "ecchi" },
    { name: "Harem", slug: "harem" },
    { name: "Horror", slug: "horror" },
    { name: "Mystery", slug: "mystery" },
    { name: "School", slug: "school" },
    { name: "School Life", slug: "school-life" },
    { name: "Sci-Fi", slug: "sci-fi" },
    { name: "Slice of Life", slug: "slice-of-life" },
    { name: "Supernatural", slug: "supernatural" },
    { name: "Martial Arts", slug: "martial-arts" },
    { name: "Shounen", slug: "shounen" },
    { name: "Seinen", slug: "seinen" },
    { name: "Shoujo", slug: "shoujo" },
    { name: "Mature", slug: "mature" },
    { name: "Adult", slug: "adult" },
    { name: "Gore", slug: "gore" },
    { name: "Psychological", slug: "psychological" },
    { name: "Thriller", slug: "thriller" },
    { name: "Tragedy", slug: "tragedy" },
    { name: "Mecha", slug: "mecha" },
    { name: "Sports", slug: "sports" },
    { name: "Historical", slug: "historical" },
    { name: "Magic", slug: "magic" },
    { name: "Reincarnation", slug: "reincarnation" },
    { name: "Webtoons", slug: "webtoons" },
    { name: "Yuri", slug: "yuri" },
    { name: "Shoujo Ai", slug: "shoujo-ai" },
    { name: "Shounen Ai", slug: "shounen-ai" },
    { name: "Josei", slug: "josei" },
    { name: "Demons", slug: "demons" },
    { name: "Vampire", slug: "vampire" },
    { name: "Game", slug: "game" },
    { name: "Cooking", slug: "cooking" },
    { name: "Music", slug: "music" },
    { name: "Medical", slug: "medical" },
    { name: "Military", slug: "military" },
    { name: "Police", slug: "police" },
    { name: "Gender Bender", slug: "gender-bender" },
    { name: "One-Shot", slug: "one-shot" },
    { name: "4-Koma", slug: "4-koma" },
    { name: "Super Power", slug: "super-power" },
  ];

  async function loadGenres(opts = {}) {
    const force = !!(opts && opts.force);
    const bar = document.getElementById("genre-bar");
    const sheetBar = document.getElementById("sheet-genre-bar");
    const mobileBar = document.getElementById("genre-bar-mobile");
    if (!bar && !sheetBar && !mobileBar) return;

    const chipCount = (el) =>
      el ? el.querySelectorAll("[data-filter-genre]").length : 0;
    // Need more than just "Semua" (empty data-filter-genre)
    const hasRealChips = (el) => {
      if (!el) return false;
      return [...el.querySelectorAll("[data-filter-genre]")].some(
        (n) => (n.getAttribute("data-filter-genre") || "").trim() !== ""
      );
    };

    if (
      !force &&
      genresLoaded &&
      (hasRealChips(mobileBar) || hasRealChips(bar) || hasRealChips(sheetBar))
    ) {
      const active = (ctx.state.genre || "").toLowerCase();
      document.querySelectorAll("[data-filter-genre]").forEach((el) => {
        el.classList.toggle(
          "is-active",
          (el.getAttribute("data-filter-genre") || "").toLowerCase() === active
        );
      });
      return;
    }

    const setLoading = (el) => {
      if (!el) return;
      el.dataset.loading = "1";
      el.innerHTML = '<span class="chip chip--muted">Memuat genre…</span>';
    };
    setLoading(bar);
    setLoading(mobileBar);
    if (sheetBar) {
      sheetBar.dataset.loading = "1";
      sheetBar.innerHTML = '<span class="chip chip--muted">Memuat genre…</span>';
    }

    let list = [];
    try {
      const res = await api("genres", {}, { force: !!force, ttl: force ? 0 : 6 * 60 * 60_000 });
      list = Array.isArray(res?.data) ? res.data.slice() : [];
    } catch (e) {
      console.warn("genres api", e);
    }
    if (!list.length) {
      list = FALLBACK_GENRES.slice();
    }

    const PRIORITY = [
      "Action", "Adventure", "Romance", "Fantasy", "Isekai", "Comedy", "Drama",
      "Ecchi", "Harem", "Horror", "Mystery", "School", "School Life", "Sci-Fi",
      "Slice of Life", "Supernatural", "Martial Arts", "Shounen", "Seinen",
      "Shoujo", "Mature", "Adult",
    ];
    const rank = new Map(PRIORITY.map((n, i) => [n.toLowerCase(), i]));
    list.sort((a, b) => {
      const na = (a.name || (a.data && a.data.name) || "").trim();
      const nb = (b.name || (b.data && b.data.name) || "").trim();
      const ra = rank.has(na.toLowerCase()) ? rank.get(na.toLowerCase()) : 1000;
      const rb = rank.has(nb.toLowerCase()) ? rank.get(nb.toLowerCase()) : 1000;
      if (ra !== rb) return ra - rb;
      return na.localeCompare(nb, "en", { sensitivity: "base" });
    });

    const active = (ctx.state.genre || "").toLowerCase();

    function fill(container, allLabel) {
      if (!container) return;
      container.innerHTML = "";
      delete container.dataset.loading;
      const allBtn = document.createElement("button");
      allBtn.type = "button";
      allBtn.className = "chip" + (!active ? " is-active" : "");
      allBtn.setAttribute("data-filter-genre", "");
      allBtn.textContent = allLabel;
      allBtn.addEventListener("click", () => {
        if (window.App && App.setFilter) App.setFilter("genre", "");
        try { closeFilterSheet(); } catch (_) {}
      });
      container.appendChild(allBtn);
      list.forEach((g) => {
        const name = (g.name || (g.data && g.data.name) || "").trim();
        if (!name) return;
        const btn = document.createElement("button");
        btn.type = "button";
        const on = active === name.toLowerCase();
        btn.className = "chip" + (on ? " is-active" : "");
        btn.setAttribute("data-filter-genre", name);
        btn.title = name;
        btn.textContent = name;
        btn.addEventListener("click", () => {
          if (window.App && App.setFilter) App.setFilter("genre", name);
          try { closeFilterSheet(); } catch (_) {}
        });
        container.appendChild(btn);
      });
    }

    fill(bar, "Semua genre");
    fill(sheetBar, "Semua");
    fill(mobileBar, "Semua");
    genresLoaded = list.length > 0;
    console.info("[lumen] genres loaded:", list.length);
  }

  function showSkeleton(n = 8) {
    const box = $("#series-list");
    if (!box) return;
    box.innerHTML = Array.from({ length: n }, () =>
      `<article class="card card-skeleton" aria-hidden="true">
        <div class="sk sk-cover"></div>
        <div class="card-body">
          <div class="sk sk-line"></div>
          <div class="sk sk-line sk-line-short"></div>
          <div class="sk sk-line sk-line-mid"></div>
        </div>
      </article>`
    ).join("");
  }

  function setDataBadge(kind, text) {
    const el = $("#data-badge");
    if (!el) return;
    if (!kind) {
      el.className = "data-badge is-hidden";
      el.textContent = "";
      return;
    }
    el.className = "data-badge data-badge--" + kind;
    el.textContent = text || "";
  }

  function buildListParams() {
    const params = {
      take: Config.pageSize,
      page: ctx.state.page,
      takeChapter: Config.previewChapters || 3,
      includeMeta: "true",
    };
    if (ctx.state.query) {
      params.title = ctx.state.query; // Voratoon hanya honor title=
      params.mode = "search";
      params.takeChapter = 2;
    } else if (ctx.state.tab === "browse") {
      params.mode = "browse";
      params.browse = "1";
    } else if (ctx.state.tab === "newest") {
      params.mode = "newest";
      params.sort = "updatedAt";
      params.sortOrder = "desc";
    } else if (ctx.state.tab === "new_series") {
      params.mode = "new_series";
      params.sort = "createdAt";
      params.sortOrder = "desc";
    } else if (ctx.state.tab === "completed") {
      params.mode = "completed";
      params.status = "completed";
      params.sort = "updatedAt";
    } else if (ctx.state.tab === "project") {
      params.mode = "project";
      params.type = "project";
    } else if (ctx.state.tab === "hot") {
      params.mode = "hot";
      params.sort = "popularity";
      params.sortOrder = "desc";
      params.isHot = "true";
    }
    if (ctx.state.status && ctx.state.tab !== "completed") params.status = ctx.state.status;
    if (ctx.state.format) params.format = ctx.state.format;
    if (ctx.state.genre) {
      params.genre = ctx.state.genre;
      params.mode = "browse";
      params.browse = "1";
    }
    if (ctx.state.status || ctx.state.format || ctx.state.genre) {
      params.mode = "browse";
      params.browse = "1";
    }
    return params;
  }

  function applyListData(data) {
    if (!data || typeof data !== "object") data = { data: [], meta: {} };
    let items = data.data;
    if (!Array.isArray(items)) {
      items = (items && items.data) || data.results || data.items || [];
    }
    if (!Array.isArray(items)) items = [];
    const meta = data.meta || data.pagination || {};
    ctx.state.lastPage = Number(meta.lastPage || meta.total_pages || 1) || 1;
    ctx.state.page = Number(meta.page || meta.current_page || ctx.state.page || 1) || 1;
    const pageInfo = $("#page-info");
    if (pageInfo) pageInfo.textContent = `${ctx.state.page} / ${ctx.state.lastPage}`;
    const bp = $("#btn-prev");
    const bn = $("#btn-next");
    if (bp) bp.disabled = ctx.state.page <= 1;
    if (bn) bn.disabled = ctx.state.page >= ctx.state.lastPage;
    try {
      renderList(items);
    } catch (e) {
      console.error("renderList", e);
      const box = $("#series-list");
      if (box) {
        box.innerHTML =
          '<p class="page-sub">Gagal menampilkan kartu. <button type="button" class="btn" id="retry-render">Coba lagi</button></p>';
        const b = document.getElementById("retry-render");
        if (b) b.onclick = () => loadList({ force: true });
      }
    }
    try {
      renderContinue();
    } catch (_) {}
    const st = $("#list-status");
    if (st) {
      st.textContent = items.length
        ? `${items.length} judul · ${meta.total != null ? Number(meta.total).toLocaleString("id-ID") : "—"} total`
        : "Tidak ada hasil.";
    }
  }

  async function loadList(opts = {}) {
    try {
      loadGenres();
    } catch (e) {
      console.warn("loadGenres", e);
    }
    const force = !!opts.force;
    const params = buildListParams();
    const box = $("#series-list");

    if (force) {
      clearApiCache();
      setDataBadge("fresh", "Memuat ulang…");
    }

    const cached = force ? null : apiPeek("series", params);

    // Cache hit → tampil instan, revalidate di belakang
    if (cached) {
      applyListData(cached);
      const fromDb = cached.meta && cached.meta.source === "sqlite";
      setDataBadge(
        fromDb ? "db" : "cache",
        fromDb ? "Data tersimpan (DB) — mungkin belum update terbaru" : "Dari cache — sedang diperbarui…"
      );
      loading(false);
      api("series", params, { ttl: 90_000, stale: 5 * 60_000, force: false })
        .then((data) => {
          applyListData(data);
          const db = data.meta && data.meta.source === "sqlite";
          setDataBadge(db ? "db" : "live", db ? "Data tersimpan (DB)" : "Data terbaru");
          setTimeout(() => setDataBadge(null), 2500);
        })
        .catch(() => {
          setDataBadge("cache", "Menampilkan cache (update gagal)");
        });
      return;
    }

    const empty = !box || !box.children.length;
    if (empty) loading(true);
    showSkeleton(10);
    $("#list-status").textContent = "Memuat…";
    const t0 = Date.now();
    let slowTimer = setTimeout(() => {
      toast("Server sedang bangun… tunggu sebentar");
    }, 3500);
    try {
      let data;
      try {
        data = await api("series", params, {
          ttl: 90_000,
          stale: 5 * 60_000,
          force,
        });
      } catch (netErr) {
        if (ctx.state.query && ctx.state.query.trim().length >= 2) {
          data = await api("local/search", {
            q: ctx.state.query.trim(),
            limit: Config.pageSize,
          });
          if (!(data.data || []).length) throw netErr;
          toast("Menampilkan hasil dari cache lokal");
          setDataBadge("db", "Hasil pencarian lokal (DB)");
        } else {
          throw netErr;
        }
      }
      applyListData(data);
      const db = data.meta && data.meta.source === "sqlite";
      setDataBadge(db ? "db" : "live", db ? "Data tersimpan (DB)" : "Data terbaru");
      setTimeout(() => setDataBadge(null), 2500);
    } catch (err) {
      console.error(err);
      const raw = String(err.message || err);
      const msg = friendlyError(raw);
      const isEmpty = /kosong|empty|tidak ada data/i.test(raw);
      $("#list-status").textContent = msg;
      setDataBadge(null);
      renderState($("#series-list"), {
        title: isEmpty ? "Belum ada data" : "Gagal memuat daftar",
        detail: msg,
        retryLabel: "Coba lagi",
        onRetry: () => loadList({ force: true }),
      });
      toast(msg);
    } finally {
      try { clearTimeout(slowTimer); } catch (_) {}
      if (Date.now() - t0 > 8000) {
        /* cold start recovery tip once */
      }
      loading(false);
    }
  }

  function renderList(items) {
    try { renderHero(items); } catch (_) {}
    const box = $("#series-list");
    if (!box) return;
    // Batch DOM: single reflow via DocumentFragment
    const frag = document.createDocumentFragment();
    const list = Array.isArray(items) ? items : [];
    list.forEach((item, i) => {
      const d = item.data || {};
      const chapters = item.chapters || [];
      const card = document.createElement("article");
      card.className = "card";
      card.onclick = () => ctx.openSeries(d.slug || item.id, {
        title: d.title || "",
        cover: d.coverImage || d.cover || "",
      });

      const badges = [];
      if (d.isHot) badges.push('<span class="badge badge--hot">Hot</span>');
      if (d.type === "project") badges.push('<span class="badge badge--up">Project</span>');
      if (d.format) badges.push(`<span class="badge">${esc(d.format)}</span>`);
      if (d.status) badges.push(`<span class="badge">${esc(d.status)}</span>`);

      let chHtml = chapters
        .slice(0, Config.previewChapters)
        .map((ch) => {
          const idx = chapterIndex(ch) ?? "?";
          const t = relTime(ch.createdAt || ch.updatedAt);
          const neu = isNew(ch.createdAt || ch.updatedAt);
          return `<div>Ch. ${esc(String(idx))}${neu ? ' <span class="new">BARU</span>' : ""}${t ? " · " + esc(t) : ""}</div>`;
        })
        .join("");
      if (!chHtml) {
        const label = d.latestChapterLabel || (d.totalChapters != null ? `Ch. ${d.totalChapters}` : "");
        const t = relTime(d.updatedLabel || item.updatedAt);
        chHtml = label
          ? `<div>${esc(label)}${t ? " · " + esc(t) : ""}</div>`
          : `<div>Total ch. ${esc(String(d.totalChapters ?? "—"))}</div>`;
      }

      const eager = i < 4;
      card.innerHTML = `
        <img class="card-cover" alt="" ${eager ? 'loading="eager" fetchpriority="high"' : 'loading="lazy"'} decoding="async" width="360" height="540" />
        <div class="card-body">
          <div class="card-title">${esc(d.title || "—")}</div>
          <div class="badges">${badges.join("")}</div>
          <div class="card-chapters">${chHtml}</div>
        </div>`;
      const img = card.querySelector("img");
      if (eager && img) img.fetchPriority = "high";
      setImg(img, d.coverImage || d.cover || "", { w: 360, cover: true });
      frag.appendChild(card);
    });
    box.replaceChildren(frag);
  }

  function setTab(name) {
    ctx.state.tab = name;
    ctx.state.page = 1;
    ctx.state.query = "";
    const q = $("#q");
    if (q) q.value = "";
    syncNav(name);
    const titles = {
      newest: "Terbaru",
      new_series: "Series Baru",
      completed: "Selesai",
      browse: "Browse",
      project: "Project",
      hot: "Populer",
    };
    $("#list-title").textContent = titles[name] || name;
    try {
      navigate({ name: "home", tab: name, query: "" }, { replace: false });
    } catch (_) {}
    try {
      const metaMap = {
        newest: { t: "Terbaru — Lumen", d: "Update chapter komik terbaru setiap hari." },
        new_series: { t: "Series Baru — Lumen", d: "Series komik baru di Lumen." },
        completed: { t: "Selesai — Lumen", d: "Komik completed / tamat." },
        browse: { t: "Browse — Lumen", d: "Jelajahi katalog komik dan genre." },
        hot: { t: "Populer — Lumen", d: "Komik populer di Lumen." },
        project: { t: "Project — Lumen", d: "Series project di Lumen." },
      };
      const m = metaMap[name] || metaMap.newest;
      clearJsonLd();
      const tabUrl =
        name === "hot"
          ? "/popular"
          : name === "newest" || !name
            ? "/"
            : `/latest?tab=${encodeURIComponent(name)}`;
      setMeta({
        title: m.t,
        description: m.d,
        url: tabUrl,
        keywords: "manga, manhwa, manhua, komik online, baca komik, " + (name || "terbaru"),
      });
      try {
        setJsonLd({
          "@context": "https://schema.org",
          "@graph": [
            websiteJsonLd(),
            breadcrumbJsonLd([
              { name: "Beranda", path: "/" },
              { name: (m.t || "Terbaru").split("—")[0].trim(), path: tabUrl },
            ]),
          ],
        });
      } catch (_) {}
    } catch (_) {}
    showView("home");
    loadList();
  }

  function page(delta) {
    const next = ctx.state.page + delta;
    if (next < 1 || next > ctx.state.lastPage) return;
    ctx.state.page = next;
    loadList();
  }

  function search(e) {
    if (e && e.preventDefault) e.preventDefault();
    ctx.state.query = (($("#q") && $("#q").value) || "").trim();
    ctx.state.page = 1;
    if (!ctx.state.query) {
      // clear search → back to current tab feed
      try {
        navigate({ name: "home", tab: ctx.state.tab || "newest", query: "" }, { replace: true });
      } catch (_) {}
      $("#list-title").textContent = "Terbaru";
      showView("home");
      loadList();
      return false;
    }
    $("#list-title").textContent = `Hasil untuk "${ctx.state.query}"`;
    try {
      navigate({ name: "home", tab: "newest", query: ctx.state.query }, { replace: false });
    } catch (_) {}
    try {
      clearJsonLd();
      setMeta({
        title: `Cari "${ctx.state.query}" — Lumen`,
        description: `Hasil pencarian komik untuk ${ctx.state.query}`,
        url: `/search?q=${encodeURIComponent(ctx.state.query)}`,
      });
    } catch (_) {}
    showView("home");
    loadList();
    return false;
  }



  function renderContinue() {
    const box = $("#continue-banner");
    if (!box) return;
    const last = getLastRead();
    if (!last || !last.slug) {
      box.classList.add("is-hidden");
      box.innerHTML = "";
      return;
    }
    box.classList.remove("is-hidden");
    const cover = last.cover || last.coverImage || "";
    const coverSrc = cover ? proxyImageUrl(cover, { webp: true, w: 360 }) : "";
    box.innerHTML = `
      <button type="button" class="continue-card" id="btn-continue">
        ${coverSrc ? `<img class="continue-cover" alt="" src="${esc(coverSrc)}" loading="lazy" decoding="async" />` : `<div class="continue-cover"></div>`}
        <div class="continue-text">
          <div class="continue-label">Continue Reading</div>
          <div class="continue-title">${esc(last.title || last.slug)}</div>
          <div class="continue-meta">Chapter ${esc(String(last.chapter ?? "—"))}</div>
        </div>
        <span class="continue-go">Open →</span>
      </button>`;
    const btn = $("#btn-continue");
    if (btn) {
      btn.onclick = async () => {
        try {
          await ctx.openSeries(last.slug);
          await ctx.openChapter(String(last.chapter));
        } catch (e) {
          console.error(e);
        }
      };
    }
  }


  function setFilter(kind, value) {
    if (kind === "status") ctx.state.status = value || "";
    if (kind === "format") ctx.state.format = value || "";
    if (kind === "genre") ctx.state.genre = value || "";
    if (ctx.state.status || ctx.state.format || ctx.state.genre) {
      if (ctx.state.tab === "newest" || ctx.state.tab === "hot") {
        ctx.state.tab = "browse";
      }
      document.querySelectorAll(".tab").forEach((t) =>
        t.classList.toggle("is-active", t.dataset.tab === "browse")
      );
      const titleEl = $("#list-title");
      if (titleEl) {
        titleEl.textContent = ctx.state.genre ? `Genre: ${ctx.state.genre}` : "Browse";
      }
    }
    const attr = "data-filter-" + kind;
    document.querySelectorAll("[" + attr + "]").forEach((el) => {
      el.classList.toggle("is-active", (el.getAttribute(attr) || "") === (value || ""));
    });
    if (kind === "genre") {
      document.querySelectorAll("[data-filter-genre]").forEach((el) => {
        el.classList.toggle(
          "is-active",
          (el.getAttribute("data-filter-genre") || "") === (value || "")
        );
      });
    }
    ctx.state.page = 1;
    loadList({ force: true });
  }



  function setupPullToRefresh() {
    const scroller = document.getElementById("view-home") || document;
    let startY = 0;
    let pulling = false;
    const onStart = (e) => {
      if (window.scrollY > 8) return;
      startY = e.touches && e.touches[0] ? e.touches[0].clientY : 0;
      pulling = true;
    };
    const onMove = (e) => {
      if (!pulling || window.scrollY > 8) return;
      const y = e.touches && e.touches[0] ? e.touches[0].clientY : 0;
      if (y - startY > 90) {
        pulling = false;
        toast("Memuat ulang…");
        loadList({ force: true });
      }
    };
    const onEnd = () => {
      pulling = false;
    };
    document.addEventListener("touchstart", onStart, { passive: true });
    document.addEventListener("touchmove", onMove, { passive: true });
    document.addEventListener("touchend", onEnd, { passive: true });
  }
  setupPullToRefresh();


  function openFilterSheet() {
    const sheet = document.getElementById("filter-sheet");
    const bd = document.getElementById("filter-backdrop");
    if (!sheet || !bd) return;
    sheet.hidden = false;
    bd.hidden = false;
    sheet.classList.remove("is-hidden");
    bd.classList.remove("is-hidden");
    document.body.style.overflow = "hidden";
    // Always refresh chips when opening (avoid stuck "Semua" only)
    genresLoaded = false;
    loadGenres({ force: true });
  }
  function closeFilterSheet() {
    const sheet = document.getElementById("filter-sheet");
    const bd = document.getElementById("filter-backdrop");
    if (sheet) {
      sheet.classList.add("is-hidden");
      sheet.hidden = true;
    }
    if (bd) {
      bd.classList.add("is-hidden");
      bd.hidden = true;
    }
    document.body.style.overflow = "";
  }
  function wireMobileChrome() {
    const openBtn = document.getElementById("btn-open-filters");
    const closeBtn = document.getElementById("btn-close-filters");
    const applyBtn = document.getElementById("btn-apply-filters");
    const resetBtn = document.getElementById("btn-reset-filters");
    const bd = document.getElementById("filter-backdrop");
    if (openBtn) openBtn.onclick = () => openFilterSheet();
    if (closeBtn) closeBtn.onclick = () => closeFilterSheet();
    if (applyBtn) applyBtn.onclick = () => closeFilterSheet();
    if (bd) bd.onclick = () => closeFilterSheet();
    if (resetBtn) {
      resetBtn.onclick = () => {
        setFilter("status", "");
        setFilter("format", "");
        setFilter("genre", "");
      };
    }
    const header = document.getElementById("header");
    const st = document.getElementById("search-toggle");
    const sc = document.getElementById("search-close");
    const q = document.getElementById("q");
    if (st && header) {
      st.onclick = () => {
        header.classList.add("is-search-open");
        st.setAttribute("aria-expanded", "true");
        setTimeout(() => q && q.focus(), 30);
      };
    }
    if (sc && header) {
      sc.onclick = () => {
        header.classList.remove("is-search-open");
        if (st) st.setAttribute("aria-expanded", "false");
      };
    }
  }
  wireMobileChrome();

  return { loadList, setTab, page, search, renderContinue, setFilter };


}
