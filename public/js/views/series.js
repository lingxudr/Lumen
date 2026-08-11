import { api } from "../api.js";
import { $, esc, escAttr, relTime, isNew, chapterIndex } from "../utils.js";
import { toast, loading, showView, setImg, renderState } from "../ui.js";
import { isBookmarked, toggleBookmark, getPrefs, savePrefs } from "../storage.js";

export function createSeriesView(ctx) {
  async function openSeries(slugOrId) {
    loading(true);
    try {
      const detail = await api(`series/${encodeURIComponent(slugOrId)}`, { includeMeta: "true" }, { ttl: 5 * 60_000, stale: 30 * 60_000 });
      const series = detail.data;
      if (!series) throw new Error("Judul tidak ditemukan");
      ctx.state.series = series;
      const slug = series.data?.slug || series.slug || String(slugOrId);
      const chRes = await api(`series/${encodeURIComponent(slug)}/chapters`, {}, { ttl: 3 * 60_000, stale: 20 * 60_000 });
      ctx.state.chapters = chRes.data || [];
      showView("series");
      render();
    } catch (err) {
      console.error(err);
      const msg = String(err.message || err);
      toast(msg);
      showView("series");
      const detail = document.querySelector("#series-detail");
      if (detail) {
        renderState(detail, {
          title: "Gagal memuat judul",
          detail: msg,
          retryLabel: "Coba lagi",
          onRetry: () => openSeries(slugOrId),
        });
      }
    } finally {
      loading(false);
    }
  }

  function orderedChapters() {
    const order = getPrefs().chapterOrder || "desc";
    const list = [...(ctx.state.chapters || [])];
    list.sort((a, b) => {
      const ia = Number(chapterIndex(a)) || 0;
      const ib = Number(chapterIndex(b)) || 0;
      return order === "asc" ? ia - ib : ib - ia;
    });
    return list;
  }

  function render() {
    const s = ctx.state.series;
    const d = s.data || {};
    const meta = s.dataMetadata || s.metadata || {};
    const slug = d.slug || s.slug || "";
    const bookmarked = isBookmarked(slug);
    const order = getPrefs().chapterOrder || "desc";
    const detail = $("#series-detail");

    detail.innerHTML = `
      <img alt="" />
      <div>
        <h1>${esc(d.title || "")}</h1>
        <div class="series-meta">
          ${esc(d.author || "—")} · ${esc(d.status || "")} · ${esc(d.format || "")} ·
          ${esc(String(d.totalChapters ?? ctx.state.chapters.length))} chapter
          ${meta.dailyViews != null ? " · " + Number(meta.dailyViews).toLocaleString("id-ID") + " views" : ""}
        </div>
        <div class="badges" style="margin-bottom:12px">
          ${(d.genres || [])
            .map((g) => {
              const name = g.data?.name || g.name || "";
              return name ? `<span class="badge">${esc(name)}</span>` : "";
            })
            .join("")}
        </div>
        <div class="series-actions">
          <button type="button" class="btn ${bookmarked ? "btn-primary" : "btn-ghost"}" id="btn-bookmark">
            ${bookmarked ? "★ Favorit" : "☆ Favorit"}
          </button>
        </div>
        <div class="series-synopsis">${esc(d.synopsis || "Belum ada sinopsis.")}</div>
      </div>`;
    setImg(detail.querySelector("img"), d.coverImage || "");

    const bm = $("#btn-bookmark");
    if (bm) {
      bm.onclick = (e) => {
        e.stopPropagation();
        const on = toggleBookmark({
          slug,
          title: d.title || slug,
          cover: d.coverImage || "",
          status: d.status || "",
          format: d.format || "",
        });
        toast(on ? "Ditambahkan ke favorit" : "Dihapus dari favorit");
        render();
      };
    }

    const label = $("#chapter-list-label");
    if (label) {
      label.innerHTML = `
        <span>Daftar Chapter</span>
        <div class="seg seg-sm">
          <button type="button" class="seg-btn ${order === "desc" ? "is-active" : ""}" data-order="desc">Terbaru</button>
          <button type="button" class="seg-btn ${order === "asc" ? "is-active" : ""}" data-order="asc">Terlama</button>
        </div>`;
      label.querySelectorAll("[data-order]").forEach((btn) => {
        btn.onclick = () => {
          savePrefs({ chapterOrder: btn.dataset.order });
          render();
        };
      });
    }

    const list = $("#chapter-list");
    const chapters = orderedChapters();
    list.innerHTML = chapters
      .map((ch) => {
        const idx = chapterIndex(ch);
        const t = relTime(ch.createdAt);
        const neu = isNew(ch.createdAt);
        return `
          <div class="ch-item" data-idx="${escAttr(String(idx))}">
            <span class="num">Chapter ${esc(String(idx))}${neu ? ' <span class="badge badge--new">Baru</span>' : ""}</span>
            <span class="time">${esc(t)}</span>
          </div>`;
      })
      .join("");

    list.querySelectorAll(".ch-item").forEach((el) => {
      el.onclick = () => ctx.openChapter(el.getAttribute("data-idx"));
    });
  }

  return { openSeries, render };
}
