/**
 * Clinical-inspired eye comfort helpers (not medical treatment).
 * - 20-20-20 rest reminders (digital eye strain guidance)
 * - Optional evening auto warm/night mode (melanopic / circadian comfort)
 */
import { getPrefs, savePrefs } from "./storage.js";
import { toast } from "./ui.js";

const REST_MS = 20 * 60 * 1000; // 20 minutes
const LOOK_AWAY_SEC = 20;

let restTimer = null;
let lookAwayTimer = null;
let eveningTimer = null;

function hourLocal() {
  return new Date().getHours();
}

/** Suggest mode from clock (evening melanopic comfort heuristic). */
export function suggestedEyeCareFromTime() {
  const h = hourLocal();
  if (h >= 21 || h < 5) return "night";
  if (h >= 18) return "warm";
  return "off";
}

export function applyEveningAuto(setEyeCareFn) {
  const prefs = getPrefs();
  if (!prefs.autoEveningEyeCare) return;
  // Don't override if user forced "off" this session? respect saved eyeCare only when auto on
  const sug = suggestedEyeCareFromTime();
  if (typeof setEyeCareFn === "function") {
    // Only auto-switch when current is off or previous auto value
    const cur = document.documentElement.getAttribute("data-eye-care") || "off";
    if (cur === sug) return;
    if (prefs.eyeCareManual) return; // user picked manually
    setEyeCareFn(sug);
  }
}

function showRestOverlay() {
  let el = document.getElementById("eye-rest-overlay");
  if (!el) {
    el = document.createElement("div");
    el.id = "eye-rest-overlay";
    el.className = "eye-rest-overlay";
    el.innerHTML = `
      <div class="eye-rest-card" role="dialog" aria-labelledby="eye-rest-title">
        <p class="eye-rest-kicker">Istirahat mata · aturan 20-20-20</p>
        <h2 id="eye-rest-title">Lihat jauh ~6 meter</h2>
        <p class="eye-rest-desc">Setiap 20 menit, lihat objek jauh selama 20 detik. Ini praktik ergonomi visual yang umum disarankan untuk mengurangi kelelahan saat layar (bukan obat).</p>
        <div class="eye-rest-count" id="eye-rest-count">20</div>
        <div class="eye-rest-actions">
          <button type="button" class="btn btn-primary" id="eye-rest-start">Mulai 20 detik</button>
          <button type="button" class="btn" id="eye-rest-skip">Nanti saja</button>
        </div>
        <p class="eye-rest-ref">Referensi praktis: istirahat berkala saat digital near work (AAO / ergonomi visual).</p>
      </div>`;
    document.body.appendChild(el);
    el.querySelector("#eye-rest-skip").onclick = () => hideRestOverlay(true);
    el.querySelector("#eye-rest-start").onclick = () => startLookAway();
    el.addEventListener("click", (e) => {
      if (e.target === el) hideRestOverlay(true);
    });
  }
  el.classList.add("is-open");
  el.setAttribute("aria-hidden", "false");
}

function hideRestOverlay(reschedule) {
  const el = document.getElementById("eye-rest-overlay");
  if (el) {
    el.classList.remove("is-open");
    el.setAttribute("aria-hidden", "true");
  }
  if (lookAwayTimer) {
    clearInterval(lookAwayTimer);
    lookAwayTimer = null;
  }
  if (reschedule) scheduleRestReminder();
}

function startLookAway() {
  const countEl = document.getElementById("eye-rest-count");
  let left = LOOK_AWAY_SEC;
  if (countEl) countEl.textContent = String(left);
  if (lookAwayTimer) clearInterval(lookAwayTimer);
  lookAwayTimer = setInterval(() => {
    left -= 1;
    if (countEl) countEl.textContent = String(Math.max(0, left));
    if (left <= 0) {
      clearInterval(lookAwayTimer);
      lookAwayTimer = null;
      hideRestOverlay(true);
      try {
        toast("Siap lanjut baca");
      } catch (_) {}
    }
  }, 1000);
}

export function scheduleRestReminder() {
  if (restTimer) {
    clearTimeout(restTimer);
    restTimer = null;
  }
  const prefs = getPrefs();
  if (!prefs.restReminder) return;
  restTimer = setTimeout(() => {
    if (document.visibilityState === "hidden") {
      scheduleRestReminder();
      return;
    }
    showRestOverlay();
  }, REST_MS);
}

export function stopRestReminder() {
  if (restTimer) clearTimeout(restTimer);
  restTimer = null;
  hideRestOverlay(false);
}

export function initEyeCareClinical(setEyeCareFn) {
  scheduleRestReminder();
  applyEveningAuto(setEyeCareFn);
  if (eveningTimer) clearInterval(eveningTimer);
  eveningTimer = setInterval(() => applyEveningAuto(setEyeCareFn), 15 * 60 * 1000);

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      scheduleRestReminder();
      applyEveningAuto(setEyeCareFn);
    }
  });
}

export function setRestReminder(on) {
  savePrefs({ restReminder: !!on });
  if (on) scheduleRestReminder();
  else stopRestReminder();
}

export function setAutoEvening(on) {
  savePrefs({ autoEveningEyeCare: !!on, eyeCareManual: false });
}

export function markEyeCareManual() {
  savePrefs({ eyeCareManual: true });
}
