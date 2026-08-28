/** SEO meta + JSON-LD helpers for Lumen SPA */

const SITE = "https://www.v1lumen.my.id";
const SITE_NAME = "Lumen";
const DEFAULT_DESC =
  "Baca manga, manhwa, dan manhua online gratis. Update chapter terbaru setiap hari di Lumen.";
const DEFAULT_IMG = SITE + "/icons/icon-512.png";

function absUrl(url) {
  if (!url) return SITE + "/";
  if (/^https?:\/\//i.test(url)) return url;
  if (url.startsWith("/")) return SITE + url;
  return SITE + "/" + url;
}

function _set(attr, key, value) {
  if (value == null || value === "") return;
  let el = document.querySelector(`meta[${attr}="${String(key).replace(/"/g, '')}"]`);
  if (!el) {
    el = document.createElement("meta");
    el.setAttribute(attr, key);
    document.head.appendChild(el);
  }
  el.setAttribute("content", String(value).slice(0, attr === "name" && key === "description" ? 320 : 500));
}

function _setLink(rel, href, attrs = {}) {
  let el = document.querySelector(`link[rel="${rel}"]`);
  if (!el) {
    el = document.createElement("link");
    el.rel = rel;
    document.head.appendChild(el);
  }
  el.href = href;
  Object.entries(attrs).forEach(([k, v]) => {
    if (v) el.setAttribute(k, v);
  });
}

export function setMeta({
  title,
  description,
  image,
  url,
  type = "website",
  noindex = false,
  keywords,
} = {}) {
  const fullTitle = title
    ? title.includes(SITE_NAME)
      ? title
      : `${title} — ${SITE_NAME}`
    : `${SITE_NAME} — Baca Komik Online`;
  const desc = (description || DEFAULT_DESC).replace(/\s+/g, " ").trim().slice(0, 300);
  const pageUrl = absUrl(
    url ||
      (typeof location !== "undefined" ? location.pathname + location.search : "/")
  );
  const img = absUrl(image || DEFAULT_IMG);

  document.title = fullTitle;
  _set("property", "og:title", fullTitle);
  _set("name", "twitter:title", fullTitle);
  _set("name", "description", desc);
  _set("property", "og:description", desc);
  _set("name", "twitter:description", desc);
  _set("property", "og:image", img);
  _set("name", "twitter:image", img);
  _set("property", "og:url", pageUrl);
  _set("property", "og:type", type);
  _set("property", "og:site_name", SITE_NAME);
  _set("property", "og:locale", "id_ID");
  _set("name", "twitter:card", "summary_large_image");
  _set("name", "robots", noindex ? "noindex,nofollow" : "index,follow,max-image-preview:large");
  _set("name", "googlebot", noindex ? "noindex,nofollow" : "index,follow,max-image-preview:large");
  if (keywords) _set("name", "keywords", keywords);

  _setLink("canonical", pageUrl);
  // hreflang single-locale
  _setLink("alternate", pageUrl, { hreflang: "id" });
  _setLink("alternate", pageUrl, { hreflang: "x-default" });
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

/** Multiple JSON-LD blocks (graph) */
export function setJsonLdGraph(items) {
  const list = (Array.isArray(items) ? items : [items]).filter(Boolean);
  setJsonLd({
    "@context": "https://schema.org",
    "@graph": list.map((item) => {
      const { "@context": _, ...rest } = item;
      return rest;
    }),
  });
}

export function clearJsonLd() {
  const el = document.getElementById("json-ld");
  if (el) el.remove();
}

export function websiteJsonLd() {
  return {
    "@context": "https://schema.org",
    "@type": "WebSite",
    name: SITE_NAME,
    url: SITE + "/",
    description: DEFAULT_DESC,
    inLanguage: "id-ID",
    potentialAction: {
      "@type": "SearchAction",
      target: {
        "@type": "EntryPoint",
        urlTemplate: SITE + "/search?q={search_term_string}",
      },
      "query-input": "required name=search_term_string",
    },
    publisher: {
      "@type": "Organization",
      name: SITE_NAME,
      url: SITE + "/",
    },
  };
}

export function seriesJsonLd({ title, slug, description, image, genres, status } = {}) {
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
    isAccessibleForFree: true,
    creativeWorkStatus: status || undefined,
    publisher: {
      "@type": "Organization",
      name: SITE_NAME,
      url: SITE + "/",
    },
  };
}

export function breadcrumbJsonLd(items) {
  // items: [{ name, path }]
  return {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: (items || []).map((it, i) => ({
      "@type": "ListItem",
      position: i + 1,
      name: it.name,
      item: absUrl(it.path),
    })),
  };
}

export function chapterJsonLd({ title, slug, chapter, seriesTitle } = {}) {
  const url = absUrl(
    `/manga/${encodeURIComponent(slug || "")}/chapter-${encodeURIComponent(chapter || "")}`
  );
  return {
    "@context": "https://schema.org",
    "@type": "Chapter",
    name: `${seriesTitle || title || slug} — Chapter ${chapter}`,
    url,
    isPartOf: {
      "@type": "ComicSeries",
      name: seriesTitle || title,
      url: absUrl(`/manga/${encodeURIComponent(slug || "")}`),
    },
    inLanguage: "id",
    isAccessibleForFree: true,
  };
}

export { SITE, SITE_NAME, DEFAULT_DESC };
