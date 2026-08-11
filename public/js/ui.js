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
  const header = $("#header");
  if (header) header.classList.toggle("is-hidden", name === "reader");
  document.body.classList.toggle("mode-reader", name === "reader");
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

export function setImg(el, url) {
  if (!el) return;
  if (!url) {
    el.style.opacity = "0.25";
    return;
  }
  el.referrerPolicy = "no-referrer";
  el.loading = el.loading || "lazy";
  el.decoding = "async";
  el.src = url;
  el.onerror = () => {
    if (el.dataset.proxied) {
      el.style.opacity = "0.25";
      return;
    }
    el.dataset.proxied = "1";
    el.src = proxyImageUrl(url);
  };
}
