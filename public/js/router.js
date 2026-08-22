/**
 * Path-based URL architecture (shareable + SEO-friendly)
 *
 * /                  → home /latest
 * /latest            → home newest
 * /popular           → home hot
 * /search?q=         → search
 * /manga/:slug       → series detail
 * /manga/:slug/chapter-:n  → reader
 * /read/:slug/:n     → reader (alias)
 */
export function parseLocation(loc = window.location) {
  const path = (loc.pathname || "/").replace(/\/+$/, "") || "/";
  const qs = new URLSearchParams(loc.search || "");
  const parts = path.split("/").filter(Boolean);

  if (path === "/" || path === "/latest") {
    const tab = qs.get("tab") || "newest";
    const allowed = ["newest", "new_series", "completed", "browse", "hot", "project"];
    return {
      name: "home",
      tab: allowed.includes(tab) ? tab : "newest",
      query: qs.get("q") || "",
    };
  }
  if (path === "/popular") {
    return { name: "home", tab: "hot", query: "" };
  }
  if (path === "/search") {
    return { name: "home", tab: "newest", query: qs.get("q") || "" };
  }
  if (parts[0] === "manga" && !parts[1]) {
    return { name: "home", tab: "newest", query: qs.get("q") || "" };
  }
  if (parts[0] === "manga" && parts[1]) {
    const slug = decodeURIComponent(parts[1]);
    if (parts[2] && /^chapter-/i.test(parts[2])) {
      const n = parts[2].replace(/^chapter-/i, "");
      return { name: "reader", slug, chapter: n };
    }
    if (parts[2] && /^\d/.test(parts[2])) {
      return { name: "reader", slug, chapter: parts[2] };
    }
    return { name: "series", slug };
  }
  if (parts[0] === "read" && parts[1] && parts[2]) {
    return {
      name: "reader",
      slug: decodeURIComponent(parts[1]),
      chapter: parts[2],
    };
  }
  return { name: "home", tab: "newest", query: "" };
}

export function pathFor(route) {
  if (!route || route.name === "home") {
    if (route?.query) return `/search?q=${encodeURIComponent(route.query)}`;
    if (route?.tab === "hot") return "/popular";
    if (route?.tab === "completed") return "/latest?tab=completed";
    if (route?.tab === "new_series") return "/latest?tab=new_series";
    if (route?.tab === "browse") return "/latest?tab=browse";
    return "/latest";
  }
  if (route.name === "series") return `/manga/${encodeURIComponent(route.slug)}`;
  if (route.name === "reader") {
    return `/manga/${encodeURIComponent(route.slug)}/chapter-${route.chapter}`;
  }
  return "/";
}

export function navigate(route, { replace = false } = {}) {
  const url = pathFor(route);
  if (replace) history.replaceState(route, "", url);
  else history.pushState(route, "", url);
}
