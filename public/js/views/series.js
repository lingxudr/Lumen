import { api } from "../api.js";
import { $, esc, escAttr, relTime, isNew, chapterIndex } from "../utils.js";
import { setMeta, setJsonLdGraph, seriesJsonLd, breadcrumbJsonLd } from "../seo.js";
import { proxyImageUrl } from "../api.js";
import { toast, loading, showView, setImg, renderState } from "../ui.js";
import { isBookmarked, toggleBookmark, getPrefs, savePrefs } from "../storage.js";

/** Normalize series detail payload (Sanka / KC / hybrid). */
function normalizeSeries(payload) {
  if (!payload) return null;
  // api() returns full JSON { status, data }
  let root = payload.data != null ? payload.data : payload;
  if (!root || typeof root !== "object") return null;
  // wrapper { id, data: { title, coverImage, ... } }
  let inner = root.data && typeof root.data === "object" && !Array.isArray(root.data)
    ? root.data
    : root;
  // some paths nest again
  if (inner.data && typeof inner.data === "object" && (inner.data.title || inner.data.coverImage)) {
    inner = { ...inner, ...inner.data };
  }
  const slug =
    inner.slug ||
    root.slug ||
    root.id ||
    inner.mangaId ||
    inner.manga_id ||
    null;
  const title = inner.title || root.title || "";
  const coverImage =
    inner.coverImage ||
    inner.cover ||
    inner.cover_url ||
    inner.thumbnail ||
    "";
  const synopsis =
    inner.synopsis ||
    inner.description ||
    inner.summary ||
    "";
  const totalChapters =
    inner.totalChapters ??
    inner.total_chapters ??
    inner.latest_chapter ??
    null;
  return {
    raw: root,
    slug: slug ? String(slug) : "",
    title: title || "Tanpa judul",
    coverImage,
    synopsis,
    author: inner.author || inner.authors?.[0]?.name || "",
    status: inner.status || "",
    format: Array.isArray(inner.format) ? (inner.format[0]?.name || inner.format[0] || "") : (inner.format || inner.type || ""),
    type: inner.type || "",
    genres: inner.genres || [],
    totalChapters,
    rating: inner.rating,
    nativeTitle: inner.nativeTitle || inner.alternative_title || "",
    // shape expected by rest of app
    data: {
      ...inner,
      slug: slug ? String(slug) : inner.slug,
      title: title || inner.title,
      coverImage,
      synopsis,
      totalChapters,
    },
    id: root.id || slug,
  };
}

function normalizeChapters(payload) {
  if (!payload) return [];
  let list = payload.data != null ? payload.data : payload;
  if (!Array.isArray(list)) {
    if (list && Array.isArray(list.data)) list = list.data;
    else if (list && Array.isArray(list.chapters)) list = list.chapters;
    else return [];
  }
  return list.map((ch, i) => {
    if (!ch || typeof ch !== "object") return null;
    const d = ch.data && typeof ch.data === "object" ? ch.data : {};
    const index =
      ch.chapterIndex ??
      d.index ??
      ch.index ??
      ch.chapter_number ??
      d.chapter_number ??
      null;
    return {
      ...ch,
      data: {
        ...d,
        index: index != null ? Number(index) : d.index,
        title: d.title || ch.title || (index != null ? `Chapter ${index}` : ""),
        chapterId: d.chapterId || d.chapter_id || ch.id || ch.chapter_id,
      },
      createdAt: ch.createdAt || ch.release_date || d.release_date || ch.published_at,
      updatedAt: ch.updatedAt || ch.updated_at,
      chapterIndex: index != null ? Number(index) : null,
    };
  }).filter(Boolean);
}

export function createSeriesView(ctx) {
  function showSeriesSkeleton(hint) {
    const detail = $("#series-detail");
    const list = $("#chapter-list");
    if (detail) {
      const title = hint?.title ? esc(hint.title) : "";
      const cover = hint?.cover || "";
      detail.innerHTML = `
        <div class="series-skeleton">
          <div class="sk sk-cover-lg" ${cover ? `style="background-image:url('${escAttr(cover)}');background-size:cover"` : ""}></div>
          <div class="series-skeleton-body">
            ${title ? `<div class="series-title" style="margin:0 0 8px">${title}</div>` : `<div class="sk sk-line"></div>`}
            <div class="sk sk-line sk-line-mid"></div>
            <div class="sk sk-line sk-line-short"></div>
            <div class="sk sk-line"></div>
          </div>
        </div>`;
    }
    if (list) {
      list.innerHTML = Array.from({ length: 8 }, () =>
        `<div class="sk sk-line" style="height:44px;margin:8px 0;border-radius:8px;"></div>`
      ).join("");
    }
  }

  async function openSeries(slugOrId, hint) {
    showView("series");
    showSeriesSkeleton(hint);
    loading(true);
    const id = String(slugOrId || "").trim();
    if (!id) {
      loading(false);
      toast("Slug komik kosong");
      return;
    }
    try {
      const detailP = api(
        `series/${encodeURIComponent(id)}`,
        { includeMeta: "true" },
        { ttl: 10 * 60_000, stale: 60 * 60_000, force: false }
      );
      const chaptersP = api(
        `series/${encodeURIComponent(id)}/chapters`,
        {},
        { ttl: 5 * 60_000, stale: 30 * 60_000, force: false }
      );
      let detail;
      let chRes;
      try {
        [detail, chRes] = await Promise.all([detailP, chaptersP]);
      } catch (e) {
        // retry once with force (bypass bad client cache)
        [detail, chRes] = await Promise.all([
          api(`series/${encodeURIComponent(id)}`, { includeMeta: "true" }, { force: true }),
          api(`series/${encodeURIComponent(id)}/chapters`, {}, { force: true }),
        ]);
      }

      const series = normalizeSeries(detail);
      let chapters = normalizeChapters(chRes);

      // chapters empty → force refetch once
      if (!chapters.length) {
        try {
          const retry = await api(
            `series/${encodeURIComponent(id)}/chapters`,
            {},
            { force: true }
          );
          chapters = normalizeChapters(retry);
        } catch (_) {}
      }

      if (!series || (!series.title && !series.coverImage && !chapters.length)) {
        throw new Error("Data komik kosong / tidak ditemukan");
      }

      // fill gaps from list card hint
      if (hint) {
        if (!series.coverImage && hint.cover) series.coverImage = hint.cover;
        if (!series.data.coverImage && hint.cover) series.data.coverImage = hint.cover;
        if ((!series.title || series.title === "Tanpa judul") && hint.title) {
          series.title = hint.title;
          series.data.title = hint.title;
        }
      }

      if (!series.data.totalChapters && chapters.length) {
        series.data.totalChapters = chapters.length;
        series.totalChapters = chapters.length;
      }

      ctx.state.series = series;
      ctx.state.chapters = chapters;
      showView("series");
      try {
        const d = series.data || series;
        const slug = d.slug || series.slug || id;
        const title = d.title || slug;
        const cover = d.coverImage || d.cover || "";
        const syn = (d.synopsis || d.description || "").replace(/<[^>]+>/g, "").slice(0, 160);
        const genres = Array.isArray(d.genres)
          ? d.genres.map((g) => (typeof g === "string" ? g : g?.name)).filter(Boolean)
          : [];
        setMeta({
          title: title,
          description: syn || `Baca ${title} online di Lumen`,
          image: cover ? proxyImageUrl(cover, { webp: true, w: 600 }) : undefined,
          url: `/manga/${encodeURIComponent(slug)}`,
          type: "website",
        });
        setJsonLdGraph([seriesJsonLd({
            title,
            slug,
            description: syn,
            image: cover,
            genres,
          }), breadcrumbJsonLd([{ name: "Beranda", path: "/" }, { name: (title || slug), path: "/manga/" + encodeURIComponent(slug) }])]);
      } catch (_) {}
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
          onRetry: () => openSeries(id, hint),
        });
      }
      const list = $("#chapter-list");
      if (list) list.innerHTML = "";
    } finally {
      loading(false);
    }
  }

  function orderedChapters() {
    const order = getPrefs().chapterOrder || "desc";
    const list = [...(ctx.state.chapters || [])];
    list.sort((a, b) => {
      const ia = chapterIndex(a) ?? 0;
      const ib = chapterIndex(b) ?? 0;
      return order === "asc" ? ia - ib : ib - ia;
    });
    return list;
  }

  function render() {
    const s = ctx.state.series;
    if (!s) return;
    const d = s.data || s;
    const slug = d.slug || s.slug || "";
    const bookmarked = isBookmarked(slug);
    const order = getPrefs().chapterOrder || "desc";
    const detail = $("#series-detail");
    const chCount = d.totalChapters ?? ctx.state.chapters?.length ?? 0;

    if (detail) {
      const title = d.title || s.title || slug;
      detail.innerHTML = `
        <div class="series-hero-inner">
          <img class="series-cover" alt="${escAttr(title)}" width="200" height="300" />
          <div class="series-hero-body">
            <h1 class="series-title">${esc(title)}</h1>
            <div class="series-meta">
              ${esc([d.author, d.status, d.format || d.type, `${chCount} chapter`].filter(Boolean).join(" · "))}
            </div>
            <button type="button" class="btn" id="btn-bookmark">${bookmarked ? "★ Favorit" : "☆ Favorit"}</button>
            <div class="series-synopsis">${esc(d.synopsis || "Belum ada sinopsis.")}</div>
          </div>
        </div>`;
      setImg(detail.querySelector("img"), d.coverImage || s.coverImage || "", { w: 480 });
      const bd = document.getElementById("series-backdrop");
      if (bd) {
        const art = d.backgroundImage || d.coverImage || s.coverImage || "";
        if (art) {
          const artUrl = proxyImageUrl(art, { webp: true, w: 900 });
          bd.style.backgroundImage = `url("${String(artUrl).replace(/"/g, "%22")}")`;
          bd.style.display = "";
        } else {
          bd.style.backgroundImage = "";
        }
      }

    }

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
    if (!list) return;
    const chapters = orderedChapters();
    if (!chapters.length) {
      list.innerHTML = `<div class="state-detail" style="padding:16px">Belum ada chapter. <button type="button" class="btn" id="btn-retry-ch">Muat ulang</button></div>`;
      const btn = $("#btn-retry-ch");
      if (btn) btn.onclick = () => openSeries(slug, { title: d.title, cover: d.coverImage });
      return;
    }
    list.innerHTML = chapters
      .map((ch) => {
        const idx = chapterIndex(ch);
        const t = relTime(ch.createdAt);
        const neu = isNew(ch.createdAt);
        return `
          <div class="ch-item" data-idx="${escAttr(String(idx))}">
            <span class="num">Chapter ${esc(String(idx ?? "?"))}${neu ? ' <span class="badge badge--new">Baru</span>' : ""}</span>
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
