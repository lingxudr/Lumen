/** SEO meta + JSON-LD helpers (SPA) */
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
    _set("property", "og:image", image);
    _set("name", "twitter:image", image);
  }
  if (url) {
    _set("property", "og:url", url);
    let link = document.querySelector('link[rel="canonical"]');
    if (!link) {
      link = document.createElement("link");
      link.rel = "canonical";
      document.head.appendChild(link);
    }
    link.href = url;
  }
  _set("property", "og:type", type);
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

function _set(attr, key, value) {
  let el = document.querySelector(`meta[${attr}="${key}"]`);
  if (!el) {
    el = document.createElement("meta");
    el.setAttribute(attr, key);
    document.head.appendChild(el);
  }
  el.setAttribute("content", value);
}
