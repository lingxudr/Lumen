/**
 * Dynamic site theme: system | dark | amoled | light | sepia
 * Persisted in lumen:readerPrefs.uiTheme (and theme for reader when synced).
 */
import { getPrefs, savePrefs } from "./storage.js";

const THEMES = ["system", "dark", "amoled", "light", "sepia"];

export function listThemes() {
  return THEMES.slice();
}

function systemPrefersDark() {
  try {
    return window.matchMedia("(prefers-color-scheme: dark)").matches;
  } catch {
    return true;
  }
}

/** Resolved theme actually applied (never "system"). */
export function resolveTheme(uiTheme) {
  const t = uiTheme || getPrefs().uiTheme || "dark";
  if (t === "system") return systemPrefersDark() ? "dark" : "light";
  if (THEMES.includes(t)) return t === "system" ? "dark" : t;
  return "dark";
}

export function applyTheme(uiTheme) {
  const prefs = getPrefs();
  const choice = THEMES.includes(uiTheme) ? uiTheme : prefs.uiTheme || "dark";
  const resolved = resolveTheme(choice);
  document.documentElement.setAttribute("data-theme", resolved);
  document.documentElement.setAttribute("data-ui-theme", choice);
  document.body.setAttribute("data-theme", resolved);
  // meta theme-color
  const meta = document.querySelector('meta[name="theme-color"]');
  const colors = {
    dark: "#06070A",
    amoled: "#000000",
    light: "#F4F5F8",
    sepia: "#F2E8D5",
  };
  if (meta) meta.setAttribute("content", colors[resolved] || colors.dark);
  document.documentElement.style.colorScheme =
    resolved === "light" || resolved === "sepia" ? "light" : "dark";
  // active chips
  document.querySelectorAll("[data-ui-theme-btn]").forEach((b) => {
    b.classList.toggle("is-active", b.getAttribute("data-ui-theme-btn") === choice);
  });
  return { choice, resolved };
}

export function setUiTheme(uiTheme) {
  const choice = THEMES.includes(uiTheme) ? uiTheme : "dark";
  savePrefs({ uiTheme: choice });
  // Keep reader theme loosely in sync (except system)
  if (choice !== "system") {
    savePrefs({ theme: choice === "light" ? "sepia" : choice });
  }
  return applyTheme(choice);
}

export function initTheme() {
  const prefs = getPrefs();
  const choice = prefs.uiTheme || prefs.theme || "dark";
  applyTheme(THEMES.includes(choice) ? choice : "dark");
  try {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => {
      const p = getPrefs();
      if ((p.uiTheme || "dark") === "system") applyTheme("system");
    };
    if (mq.addEventListener) mq.addEventListener("change", onChange);
    else if (mq.addListener) mq.addListener(onChange);
  } catch (_) {}
}
