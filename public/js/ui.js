import { $, $$ } from "./utils.js";
import { proxyImageUrl } from "./api.js";

export function toast(msg, ms = 2800) {
  const el = $("#toast");
  if (!el) return;
  el.textContent = msg;
  el.classList.remove("is-hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.add("is-hidden"), ms);
}

export function loading(on) {
  const el = $("#loading");
  if (el) el.classList.toggle("is-hidden", !on);
}

export function showView(name) {
  $$(".view").forEach((v) => v.classList.add("is-hidden"));
  const el = $(`#view-${name}`);
  if (el) el.classList.remove("is-hidden");
  const isReader = name === "reader";
  const header = $("#header");
  if (header) header.classList.toggle("is-hidden", isReader);
  const sidebar = $("#sidebar");
  if (sidebar) sidebar.classList.toggle("is-hidden", isReader);
  const bottom = $("#bottom-nav");
  if (bottom) bottom.classList.toggle("is-hidden", isReader);
  document.body.classList.toggle("mode-reader", isReader);
  document.documentElement.classList.toggle("mode-reader", isReader);
  window.scrollTo(0, 0);
}

export function setOffline(on) {
  let bar = $("#offline-bar");
  if (!bar) {
    bar = document.createElement("div");
    bar.id = "offline-bar";
    bar.className = "offline-bar is-hidden";
    bar.textContent = "Tidak ada koneksi — menampilkan data tersimpan bila ada";
    document.body.prepend(bar);
  }
  bar.classList.toggle("is-hidden", !on);
  document.body.classList.toggle("is-offline", !!on);
}

/** Empty / error panel with optional retry */
export function renderState(container, { title, detail, retryLabel, onRetry }) {
  if (!container) return;
  const btn = onRetry
    ? `<button type="button" class="btn btn-primary state-retry">${retryLabel || "Coba lagi"}</button>`
    : "";
  container.innerHTML = `
    <div class="state-panel">
      <div class="state-title">${title || "Tidak ada data"}</div>
      ${detail ? `<div class="state-detail">${detail}</div>` : ""}
      ${btn}
    </div>`;
  if (onRetry) {
    const b = container.querySelector(".state-retry");
    if (b) b.onclick = onRetry;
  }
}

export function setImg(el, url, opts = {}) {
  if (!el) return;
  if (!url) {
    el.style.opacity = "0.25";
    return;
  }
  el.referrerPolicy = "no-referrer";
  el.loading = el.loading || "lazy";
  el.decoding = "async";
  // Cover & UI images: proxy + WebP (mobile-first ~360px)
  const w = opts.w != null ? opts.w : 360;
  el.src = proxyImageUrl(url, { webp: true, w, cover: !!(opts && opts.cover) });
  el.onerror = () => {
    if (el.dataset.proxied === "2") {
      el.style.opacity = "0.25";
      return;
    }
    if (el.dataset.proxied === "1") {
      el.dataset.proxied = "2";
      el.src = proxyImageUrl(url, { webp: false, w });
      return;
    }
    el.dataset.proxied = "1";
    const retry = proxyImageUrl(url, { webp: true, w });
    el.src = retry + (retry.includes("?") ? "&" : "?") + "retry=1";
  };
}
