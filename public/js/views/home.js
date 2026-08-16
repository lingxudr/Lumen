import { Config } from "../config.js";
import { api, apiPeek, clearApiCache } from "../api.js";
import { $, esc, relTime, isNew, chapterIndex } from "../utils.js";
import { toast, loading, showView, setImg, renderState } from "../ui.js";
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
  if (bg && cover) bg.style.backgroundImage = `url("${cover}")`;
  box.querySelector(".home-hero-title").textContent = d.title || slug;
  box.querySelector(".home-hero-meta").textContent = meta;
  box.querySelector(".home-hero-syn").textContent = syn || "Discover this title and start reading.";
  const btn = box.querySelector("#hero-open");
  if (btn) btn.onclick = () => {
    if (typeof window.App !== "undefined" && App.openSeries) {
      App.openSeries(slug, { title: d.title, cover: d.coverImage || cover });
    }
  };
}

export function createHomeView(ctx) {
  let genresLoaded = false;

  async function loadGenres() {
    if (genresLoaded) return;
    const bar = document.getElementById("genre-bar");
    if (!bar) return;
    try {
      const res = await api("genres", {}, { ttl: 6 * 60 * 60_000 });
      const list = res?.data || [];
      if (!list.length) return;
      genresLoaded = true;
      const frag = document.createDocumentFragment();
      const all = document.createElement("button");
      all.type = "button";
      all.className = "chip is-active";
      all.dataset.filterGenre = "";
      all.textContent = "Semua genre";
      all.onclick = () => ctx.setFilter?.("genre", "") || setFilter("genre", "");
      // wire via returned setFilter after init — use App
      all.setAttribute("onclick", "App.setFilter('genre','')");
      bar.appendChild(all);
      list.slice(0, 24).forEach((g) => {
        const name = g.name || g.data?.name;
        if (!name) return;
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "chip";
        btn.dataset.filterGenre = name;
        btn.textContent = name;
        btn.setAttribute("onclick", `App.setFilter('genre','${name.replace(/'/g, "\\'")}')`);
        bar.appendChild(btn);
      });
    } catch (e) {
      console.warn("genres", e);
    }
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
      params.mode = params.mode === "newest" ? "browse" : params.mode || "browse";
    }
    if ((ctx.state.status || ctx.state.format || ctx.state.genre) && ctx.state.tab === "browse") {
      params.mode = "browse";
      params.browse = "1";
    }
    return params;
  }

  function applyListData(data) {
    if (!data || typeof data !== "object") data = { data: [], meta: {} };
    let items = data.data;
    if (!Array.isArray(items)) {
      // some proxies wrap twice
      items = (items && items.data) || data.results || data.items || [];
    }
    if (!Array.isArray(items)) items = [];
    const meta = data.meta || data.pagination || {};
    ctx.state.lastPage = meta.lastPage || 1;
    ctx.state.page = meta.page || ctx.state.page;
    $("#page-info").textContent = `${ctx.state.page} / ${ctx.state.lastPage}`;
    $("#btn-prev").disabled = ctx.state.page <= 1;
    $("#btn-next").disabled = ctx.state.page >= ctx.state.lastPage;
    renderList(items);
    renderContinue();
    $("#list-status").textContent = items.length
      ? `${items.length} judul · ${meta.total != null ? Number(meta.total).toLocaleString("id-ID") : "—"} total`
      : "Tidak ada hasil.";
  }

  async function loadList(opts = {}) {
    loadGenres();
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
    showSkeleton(8);
    $("#list-status").textContent = "Memuat…";
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
      const msg = String(err.message || err);
      $("#list-status").textContent = msg;
      setDataBadge(null);
      renderState($("#series-list"), {
        title: "Gagal memuat daftar",
        detail: msg,
        retryLabel: "Coba lagi",
        onRetry: () => loadList({ force: true }),
      });
      toast(msg);
    } finally {
      loading(false);
    }
  }

  function renderList(items) {
    const box = $("#series-list");
    box.innerHTML = "";
    items.forEach((item) => {
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

      card.innerHTML = `
        <img class="card-cover" alt="" loading="lazy" />
        <div class="card-body">
          <div class="card-title">${esc(d.title || "—")}</div>
          <div class="badges">${badges.join("")}</div>
          <div class="card-chapters">${chHtml}</div>
        </div>`;
      setImg(card.querySelector("img"), d.coverImage || "");
      box.appendChild(card);
    });
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
    e.preventDefault();
    ctx.state.query = (($("#q") && $("#q").value) || "").trim();
    ctx.state.page = 1;
    $("#list-title").textContent = ctx.state.query ? `Hasil untuk "${ctx.state.query}"` : "Terbaru";
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
    box.innerHTML = `
      <button type="button" class="continue-card" id="btn-continue">
        ${cover ? `<img class="continue-cover" alt="" src="${esc(cover)}" loading="lazy" />` : `<div class="continue-cover"></div>`}
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
    // Filter katalog → mode browse (bukan feed terbaru RSC)
    if ((ctx.state.status || ctx.state.format || ctx.state.genre) && ctx.state.tab === "newest") {
      ctx.state.tab = "browse";
      document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("is-active", t.dataset.tab === "browse"));
      const titleEl = $("#list-title");
      if (titleEl) titleEl.textContent = "Browse";
    }
    document.querySelectorAll(`[data-filter-${kind}]`).forEach((el) => {
      el.classList.toggle("is-active", (el.getAttribute(`data-filter-${kind}`) || "") === (value || ""));
    });
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

  return { loadList, setTab, page, search, renderContinue, setFilter };


}
