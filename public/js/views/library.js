import { $, esc, escAttr } from "../utils.js";
import { showView, setImg, toast } from "../ui.js";
import { getBookmarks, getHistory, clearHistory, toggleBookmark } from "../storage.js";

/**
 * Halaman Bookmark & Riwayat (data lokal)
 */
export function createLibraryView(ctx) {
  let mode = "bookmarks"; // bookmarks | history

  function open(which) {
    mode = which === "history" ? "history" : "bookmarks";
    showView("library");
    render();
  }

  function render() {
    const title = $("#library-title");
    const sub = $("#library-status");
    const box = $("#library-list");
    if (!box) return;

    if (title) title.textContent = mode === "history" ? "Riwayat" : "Favorit";
    document.querySelectorAll("[data-lib-tab]").forEach((t) => {
      t.classList.toggle("is-active", t.dataset.libTab === mode);
    });

    const items = mode === "history" ? getHistory() : getBookmarks();
    if (sub) {
      sub.textContent = items.length
        ? `${items.length} judul tersimpan di perangkat ini`
        : mode === "history"
          ? "Belum ada riwayat baca."
          : "Belum ada favorit. Tandai dari halaman detail.";
    }

    box.innerHTML = "";
    if (!items.length) return;

    items.forEach((item) => {
      const card = document.createElement("article");
      card.className = "card";
      card.onclick = () => {
        Promise.resolve(ctx.openSeries(item.slug, { title: item.title || "", cover: item.cover || "" })).then(() => {
          if (mode === "history" && item.chapter != null) {
            setTimeout(() => ctx.openChapter(String(item.chapter)), 60);
          }
        });
      };
      const meta =
        mode === "history"
          ? `Ch. ${esc(String(item.chapter ?? "—"))}`
          : [item.format, item.status].filter(Boolean).map(esc).join(" · ");
      card.innerHTML = `
        <img class="card-cover" alt="" loading="lazy" />
        <div class="card-body">
          <div class="card-title">${esc(item.title || item.slug)}</div>
          <div class="card-chapters">${meta}</div>
        </div>`;
      setImg(card.querySelector("img"), item.cover || "", { w: 360 });
      box.appendChild(card);
    });
  }

  function onClearHistory() {
    if (!confirm("Hapus semua riwayat baca?")) return;
    clearHistory();
    toast("Riwayat dihapus");
    render();
  }

  return { open, render, onClearHistory };
}
