import { Config } from "../config.js";
import { api, apiPeek } from "../api.js";
import { $, esc, relTime, isNew, chapterIndex } from "../utils.js";
import { toast, loading, showView, setImg, renderState } from "../ui.js";
import { getLastRead } from "../storage.js";

export function createHomeView(ctx) {
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

  function buildListParams() {
    const params = { take: Config.pageSize, page: ctx.state.page };
    if (ctx.state.query) {
      params.title = ctx.state.query;
      params.takeChapter = 2;
    } else if (ctx.state.tab === "newest") {
      params.preset = "rilisan_terbaru";
      params.takeChapter = Config.previewChapters;
    } else if (ctx.state.tab === "project") {
      params.preset = "rilisan_terbaru";
      params.type = "project";
      params.takeChapter = Config.previewChapters;
    } else if (ctx.state.tab === "hot") {
      params.isHot = "true";
      params.takeChapter = 2;
      params.includeMeta = "true";
    }
    if (ctx.state.status) params.status = ctx.state.status;
    if (ctx.state.format) params.format = ctx.state.format;
    return params;
  }

  function applyListData(data) {
    const items = data.data || [];
    const meta = data.meta || {};
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

  async function loadList() {
    const params = buildListParams();
    const cached = apiPeek("series", params);
    const box = $("#series-list");

    // Cache hit → tampil instan, revalidate di belakang
    if (cached) {
      applyListData(cached);
      loading(false);
      api("series", params, { ttl: 60_000, stale: 5 * 60_000 })
        .then((data) => applyListData(data))
        .catch(() => {});
      return;
    }

    const empty = !box || !box.children.length;
    if (empty) loading(true);
    showSkeleton(8);
    $("#list-status").textContent = "Memuat…";
    try {
      let data;
      try {
        data = await api("series", params, { ttl: 90_000, stale: 10 * 60_000 });
      } catch (netErr) {
        // fallback search lokal SQLite jika user sedang mencari
        if (ctx.state.query && ctx.state.query.trim().length >= 2) {
          data = await api("local/search", { q: ctx.state.query.trim(), limit: Config.pageSize });
          if (!(data.data || []).length) throw netErr;
          toast("Menampilkan hasil dari cache lokal");
        } else {
          throw netErr;
        }
      }
      applyListData(data);
    } catch (err) {
      console.error(err);
      const msg = String(err.message || err);
      $("#list-status").textContent = msg;
      renderState($("#series-list"), {
        title: "Gagal memuat daftar",
        detail: msg,
        retryLabel: "Coba lagi",
        onRetry: () => loadList(),
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
      card.onclick = () => ctx.openSeries(d.slug || item.id);

      const badges = [];
      if (d.isHot) badges.push('<span class="badge badge--hot">Hot</span>');
      if (d.type === "project") badges.push('<span class="badge badge--up">Project</span>');
      if (d.format) badges.push(`<span class="badge">${esc(d.format)}</span>`);
      if (d.status) badges.push(`<span class="badge">${esc(d.status)}</span>`);

      const chHtml = chapters
        .slice(0, Config.previewChapters)
        .map((ch) => {
          const idx = chapterIndex(ch) ?? "?";
          const t = relTime(ch.createdAt);
          const neu = isNew(ch.createdAt);
          return `<div>Ch. ${esc(String(idx))}${neu ? ' <span class="new">BARU</span>' : ""}${t ? " · " + esc(t) : ""}</div>`;
        })
        .join("");

      card.innerHTML = `
        <img class="card-cover" alt="" loading="lazy" />
        <div class="card-body">
          <div class="card-title">${esc(d.title || "—")}</div>
          <div class="badges">${badges.join("")}</div>
          <div class="card-chapters">${chHtml || `<div>Total ch. ${esc(String(d.totalChapters ?? "—"))}</div>`}</div>
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
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("is-active", t.dataset.tab === name));
    const titles = { newest: "Terbaru", project: "Project", hot: "Populer" };
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
    box.innerHTML = `
      <button type="button" class="continue-card" id="btn-continue">
        <div class="continue-text">
          <div class="continue-label">Lanjut baca</div>
          <div class="continue-title">${esc(last.title || last.slug)}</div>
          <div class="continue-meta">Chapter ${esc(String(last.chapter ?? "—"))}</div>
        </div>
        <span class="continue-go">Buka →</span>
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
    ctx.state.page = 1;
    // highlight
    document.querySelectorAll("[data-filter-status]").forEach((b) => {
      b.classList.toggle("is-active", (b.dataset.filterStatus || "") === (ctx.state.status || ""));
    });
    document.querySelectorAll("[data-filter-format]").forEach((b) => {
      b.classList.toggle("is-active", (b.dataset.filterFormat || "") === (ctx.state.format || ""));
    });
    loadList();
  }

  return { loadList, setTab, page, search, renderContinue, setFilter };

}
