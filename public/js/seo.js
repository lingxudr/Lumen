/** SEO meta + JSON-LD helpers (SPA) */

const SITE = "https://www.v1lumen.my.id";

function absUrl(url) {
  if (!url) return SITE + "/";
  if (/^https?:\/\//i.test(url)) return url;
  if (url.startsWith("/")) return SITE + url;
  return SITE + "/" + url;
}

function _set(attr, key, value) {
  if (value == null || value === "") return;
  let el = document.querySelector(`meta[${attr}="${key}"]`);
  if (!el) {
    el = document.createElement("meta");
    el.setAttribute(attr, key);
    document.head.appendChild(el);
  }
  el.setAttribute("content", value);
}

export function setMeta({ title, description, image, url, type = "website" } = {}) {
  if (title) {
    document.title = title;
    _set("property", "og:title", title);
    _set("name", "twitter:title", title);
  }
  if (description) {
    _set("name", "description", description);
    _set("property", "og:description", description);
    _set("name", "twitter:description", description);
  }
  if (image) {
    const img = absUrl(image);
    _set("property", "og:image", img);
    _set("name", "twitter:image", img);
  }
  const pageUrl = absUrl(url || (typeof location !== "undefined" ? location.pathname + location.search : "/"));
  _set("property", "og:url", pageUrl);
  let link = document.querySelector('link[rel="canonical"]');
  if (!link) {
    link = document.createElement("link");
    link.rel = "canonical";
    document.head.appendChild(link);
  }
  link.href = pageUrl;

  _set("property", "og:type", type);
  _set("property", "og:site_name", "Lumen");
  _set("property", "og:locale", "id_ID");
  _set("name", "twitter:card", image ? "summary_large_image" : "summary");
}

export function setJsonLd(data) {
  let el = document.getElementById("json-ld");
  if (!el) {
    el = document.createElement("script");
    el.type = "application/ld+json";
    el.id = "json-ld";
    document.head.appendChild(el);
  }
  el.textContent = JSON.stringify(data);
}

export function clearJsonLd() {
  const el = document.getElementById("json-ld");
  if (el) el.remove();
}

export function seriesJsonLd({ title, slug, description, image, genres } = {}) {
  const url = absUrl(`/manga/${encodeURIComponent(slug || "")}`);
  return {
    "@context": "https://schema.org",
    "@type": "ComicSeries",
    name: title,
    url,
    description: description || undefined,
    image: image ? absUrl(image) : undefined,
    genre: Array.isArray(genres) ? genres : undefined,
    inLanguage: "id",
    publisher: { "@type": "Organization", name: "Lumen", url: SITE },
  };
}

export { SITE, absUrl };
