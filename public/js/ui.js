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
  // Sembunyikan header utama saat mode baca agar tidak mengganggu scroll
  const header = $("#header");
  if (header) header.classList.toggle("is-hidden", name === "reader");
  document.body.classList.toggle("mode-reader", name === "reader");
  window.scrollTo(0, 0);
}

/** Set image dengan fallback ke proxy jika gagal */
export function setImg(el, url) {
  if (!el) return;
  if (!url) {
    el.style.opacity = "0.25";
    return;
  }
  el.referrerPolicy = "no-referrer";
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
