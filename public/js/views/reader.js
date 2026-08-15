import { api, apiPrefetch, proxyImageUrl, checkImageStatus } from "../api.js";
import { $, esc, escAttr, chapterIndex } from "../utils.js";
import { toast, loading, showView, setImg, renderState } from "../ui.js";
import { saveLastRead, getPrefs, savePrefs, saveReadingProgress } from "../storage.js";

export function createReaderView(ctx) {
  /** @type {IntersectionObserver | null} */
  let _pageObserver = null;
  let _loadToken = 0;

  let scrollBound = false;
  let lastScrollY = 0;

  function applyPrefs() {
    const prefs = getPrefs();
    const root = document.getElementById("view-reader");
    if (root) {
      let theme = prefs.theme || "dark";
      if (theme === "light") theme = "sepia";
      root.setAttribute("data-lr-theme", theme);
      root.setAttribute("data-lr-fit", prefs.fit || "width");
      if (prefs.imgWidth) {
        root.style.setProperty("--lr-img-width", `${prefs.imgWidth}%`);
      }
      root.classList.toggle("lumen-reader--hide-progress", prefs.showProgress === false);
      root.classList.toggle("lumen-reader--no-tap", prefs.tapNav === false);
    }
    document.body.dataset.readerTheme = prefs.theme || "dark";
    document.body.dataset.readerFit = prefs.fit || "width";
    // segment active states
    document.querySelectorAll("#reader-menu [data-theme]").forEach((b) => {
      b.classList.toggle("is-active", b.dataset.theme === (prefs.theme || "dark"));
    });
    document.querySelectorAll("#reader-menu [data-fit]").forEach((b) => {
      b.classList.toggle("is-active", b.dataset.fit === (prefs.fit || "width"));
    });
    document.querySelectorAll("#reader-menu [data-mode]").forEach((b) => {
      b.classList.toggle("is-active", b.dataset.mode === (prefs.mode || "webtoon"));
    });
    let th = prefs.theme || "dark";
    if (th === "light") th = "sepia";
    document.querySelectorAll("#reader-menu [data-theme]").forEach((b) => {
      b.classList.toggle("is-active", b.dataset.theme === th);
    });
    const range = document.getElementById("reader-width-range");
    if (range && prefs.imgWidth) range.value = String(prefs.imgWidth);
    const map = {
      "pref-auto-next": prefs.autoNext !== false,
      "pref-show-progress": prefs.showProgress !== false,
      "pref-tap-nav": prefs.tapNav !== false,
      "pref-fullscreen": !!prefs.fullscreen,
    };
    Object.entries(map).forEach(([id, val]) => {
      const el = document.getElementById(id);
      if (el) el.checked = val;
    });
    if (prefs.fullscreen && document.fullscreenElement == null) {
      document.documentElement.requestFullscreen?.().catch(() => {});
    }
    if (!prefs.fullscreen && document.fullscreenElement) {
      document.exitFullscreen?.().catch(() => {});
    }
  }

  function setTheme(theme) {
    savePrefs({ theme });
    applyPrefs();
  }

  function setFit(fit) {
    savePrefs({ fit });
    applyPrefs();
  }

  function setWidth(pct) {
    const n = Math.max(60, Math.min(100, Number(pct) || 100));
    savePrefs({ imgWidth: n });
    applyPrefs();
  }

  function setPref(key, value) {
    const patch = {};
    patch[key] = value;
    savePrefs(patch);
    applyPrefs();
  }

  function updateProgress() {
    const el = document.documentElement;
    const max = el.scrollHeight - el.clientHeight;
    const ratio = max > 0 ? Math.min(1, Math.max(0, window.scrollY / max)) : 0;
    const bar = document.getElementById("reader-progress-bar");
    if (bar) bar.style.width = `${Math.round(ratio * 100)}%`;
    const pct = document.getElementById("reader-progress-pct");
    if (pct) pct.textContent = `${Math.round(ratio * 100)}%`;
  }

  function onScroll() {
    updateProgress();
    if (!document.body.classList.contains("mode-reader")) return;
    const y = window.scrollY;
    const top = $("#reader-top");
    const end = $("#reader-end");
    if (!top) return;
    const goingDown = y > lastScrollY && y > 80;
    const goingUp = y < lastScrollY;
    if (goingDown) {
      top.classList.add("is-away");
      document.body.classList.add("ui-hidden");
    } else if (goingUp || y < 40) {
      top.classList.remove("is-away");
      document.body.classList.remove("ui-hidden");
    }
    lastScrollY = y;
  }

  function bindScroll() {
    window.removeEventListener("scroll", maybeEarlyPrefetchNext);
    window.addEventListener("scroll", maybeEarlyPrefetchNext, { passive: true });

    if (scrollBound) return;
    scrollBound = true;
    window.addEventListener("scroll", onScroll, { passive: true });
  }

  function persistProgress() {
    const s = ctx.state.series;
    const d = s?.data || {};
    const slug = d.slug || s?.slug;
    if (!slug) return;
    saveLastRead({
      slug,
      title: d.title || "",
      chapter: ctx.state.chapterIndex,
      cover: d.coverImage || "",
    });
  }


  function prefetchNeighborChapters(currentIndex) {
    const slug = ctx.state.series?.data?.slug || ctx.state.series?.slug;
    if (!slug) return;
    const idxs = ctx.state.chapters.map((c) => String(chapterIndex(c)));
    const pos = idxs.indexOf(String(currentIndex));
    if (pos < 0) return;
    // prefetch tetangga segera — jangan tunggu last image
    for (const p of [pos - 1, pos + 1, pos - 2]) {
      if (p < 0 || p >= idxs.length) continue;
      apiPrefetch(
        `series/${encodeURIComponent(slug)}/chapters/${encodeURIComponent(idxs[p])}`,
        {},
        { ttl: 15 * 60_000, stale: 30 * 60_000 }
      );
    }
  }

  let _earlyPrefetchDone = false;
  function maybeEarlyPrefetchNext() {
    if (_earlyPrefetchDone) return;
    const prefs = getPrefs();
    if (prefs.autoNext === false) return;
    const el = document.documentElement;
    const max = el.scrollHeight - el.clientHeight;
    if (max <= 0) return;
    const ratio = window.scrollY / max;
    if (ratio >= 0.4) {
      _earlyPrefetchDone = true;
      prefetchNeighborChapters(ctx.state.chapterIndex);
    }
    const slug = ctx.state.series?.data?.slug || ctx.state.series?.slug;
    const images = ctx.state.chapterData?.data?.images || ctx.state.chapterData?.images || [];
    if (slug && images.length) {
      const page = Math.min(images.length - 1, Math.floor(ratio * images.length));
      saveReadingProgress(slug, ctx.state.chapterIndex, page, images.length);
    }
  }

  async function openChapter(index) {
    const slug = ctx.state.series?.data?.slug || ctx.state.series?.slug;
    if (!slug) {
      toast("Data judul tidak lengkap");
      return;
    }
    ctx.state.chapterIndex = index;
    _loadToken++;
    if (_pageObserver) {
      try { _pageObserver.disconnect(); } catch (_) {}
      _pageObserver = null;
    }
    loading(true);
    const panel = $("#hotlink-panel");
    if (panel) panel.classList.add("is-hidden");
    const menu = $("#reader-menu");
    if (menu) menu.classList.add("is-hidden");
    try {
      const res = await api(
        `series/${encodeURIComponent(slug)}/chapters/${encodeURIComponent(index)}`,
        {},
        { ttl: 15 * 60_000, stale: 45 * 60_000 }
      );
      if (!res?.data) throw new Error(res?.message || "Chapter gagal dimuat");
      ctx.state.chapterData = res.data;
      showView("reader");
      applyPrefs();
      bindScroll();
      render();
      persistProgress();
      prefetchNeighborChapters(index);
      lastScrollY = 0;
      window.scrollTo(0, 0);
      updateProgress();
    } catch (err) {
      console.error(err);
      toast("Chapter gagal dimuat");
      showView("reader");
      const box = document.querySelector("#reader-pages");
      if (box) {
        box.innerHTML = `<div class="lumen-reader-img-error img-error">
          <div class="lumen-reader-chibi" aria-hidden="true">💭</div>
          <strong>Gagal memuat chapter</strong>
          <span>Terjadi masalah saat membuka chapter. Silakan coba lagi.</span>
          <button type="button" class="lumen-reader-btn-primary" id="lr-retry-ch">Coba Lagi</button>
        </div>`;
        const btn = box.querySelector("#lr-retry-ch");
        if (btn) btn.onclick = () => openChapter(index);
        return;
      }
      if (false) {
        renderState(box, {
          title: "Gagal memuat chapter",
          detail: "Silakan coba lagi",
          retryLabel: "Coba lagi",
          onRetry: () => openChapter(index),
        });
      }
    } finally {
      loading(false);
    }
  }

  function render() {
    const ch = ctx.state.chapterData;
    const d = ctx.state.series?.data || {};
    const idx = ch?.chapterIndex ?? ctx.state.chapterIndex;
    const mangaTitleEl = document.getElementById("reader-manga-title");
    if (mangaTitleEl) mangaTitleEl.textContent = d.title || "—";
    const titleEl = $("#reader-title");
    if (titleEl) titleEl.textContent = `Chapter ${idx}`;
    const navCh = document.getElementById("reader-nav-chapter-label");
    if (navCh) navCh.textContent = `Chapter ${idx}`;
    const endLab = document.getElementById("reader-end-chapter-label");
    if (endLab) endLab.textContent = `Chapter ${idx}`;
    const cover = document.getElementById("reader-cover");
    if (cover) {
      const src = d.coverImage || d.cover || "";
      if (src) {
        cover.src = src;
        cover.classList.add("is-on");
      } else {
        cover.removeAttribute("src");
        cover.classList.remove("is-on");
      }
    }

    const images = ch?.data?.images || ch?.images || [];
    const useProxy = $("#use-proxy")?.checked;
    const box = $("#reader-pages");
    box.innerHTML = "";

    if (!images.length) {
      box.innerHTML = `<div class="lumen-reader-img-error img-error">
        <div class="lumen-reader-chibi" aria-hidden="true">💭</div>
        <strong>Gambar gagal dimuat</strong>
        <span>Terjadi masalah saat memuat gambar.</span>
        <button type="button" class="lumen-reader-btn-primary" onclick="App.reloadChapter()">Coba Lagi</button>
      </div>`;
      return;
    }

    // --- Optimized lazy loading ---
    // Adaptive buffer: slow network = smaller window
    const conn = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
    const slow = conn && (conn.saveData || /2g|slow-2g|3g/i.test(conn.effectiveType || ""));
    const PRELOAD = slow ? 2 : 4;
    const EAGER_COUNT = slow ? 1 : 2;
    const ROOT_MARGIN = slow ? "400px 0px" : "900px 0px";
    const MAX_CONCURRENT = slow ? 2 : 4;

    const resolved = images.map((url) => (useProxy ? proxyImageUrl(url, { webp: true }) : url));
    const token = ++_loadToken;

    // Tear down previous observer (chapter change / re-render)
    if (_pageObserver) {
      try { _pageObserver.disconnect(); } catch (_) {}
      _pageObserver = null;
    }

    let inFlight = 0;
    /** @type {number[]} */
    const waitQueue = [];

    function pumpQueue() {
      if (token !== _loadToken) return;
      while (inFlight < MAX_CONCURRENT && waitQueue.length) {
        const page = waitQueue.shift();
        const el = box.querySelector(`.page-slot[data-page="${page}"] img`);
        if (!el || !el.dataset.src) continue;
        inFlight++;
        const src = el.dataset.src;
        el.removeAttribute("data-src");
        el.classList.remove("img-pending");
        const done = () => {
          inFlight = Math.max(0, inFlight - 1);
          pumpQueue();
        };
        el.addEventListener("load", done, { once: true });
        el.addEventListener("error", done, { once: true });
        el.src = src;
      }
    }

    function scheduleLoad(page) {
      if (page < 0 || page >= resolved.length) return;
      const el = box.querySelector(`.page-slot[data-page="${page}"] img`);
      if (!el || !el.dataset.src) return;
      if (waitQueue.includes(page)) return;
      waitQueue.push(page);
      pumpQueue();
    }

    function scheduleWindow(center) {
      // current + forward buffer (+ 1 behind for scroll-up)
      for (let j = Math.max(0, center - 1); j <= center + PRELOAD && j < resolved.length; j++) {
        scheduleLoad(j);
      }
    }

    resolved.forEach((src, i) => {
      const wrap = document.createElement("div");
      wrap.className = "page-slot";
      wrap.dataset.page = String(i);

      const img = document.createElement("img");
      img.alt = `Halaman ${i + 1}`;
      img.decoding = "async";
      img.referrerPolicy = "no-referrer";
      // Reserve space to reduce layout jump before decode
      img.width = 800;
      img.height = 1200;
      img.style.width = "100%";
      img.style.height = "auto";
      img.onload = () => {
        img.classList.add("is-loaded");
        img.classList.remove("img-pending");
      };
      img.onerror = () => {
        if (!useProxy && !img.dataset.proxied) {
          img.dataset.proxied = "1";
          const raw = images[i];
          img.src = proxyImageUrl(raw, { webp: false });
          return;
        }
        const div = document.createElement("div");
        div.className = "img-error";
        div.innerHTML = `<strong>Gambar gagal dimuat</strong><span>Terjadi masalah saat memuat gambar.</span>`;
        const retry = document.createElement("button");
        retry.type = "button";
        retry.textContent = "Coba Lagi";
        retry.onclick = () => {
          img.classList.remove("is-loaded");
          wrap.replaceChild(img, div);
          img.src = resolved[i];
        };
        div.appendChild(retry);
        wrap.replaceChild(div, img);
      };

      if (i < EAGER_COUNT) {
        if (i === 0) img.fetchPriority = "high";
        img.loading = i === 0 ? "eager" : "lazy";
        img.src = src;
      } else {
        img.dataset.src = src;
        img.loading = "lazy";
        img.classList.add("img-pending");
      }

      wrap.appendChild(img);
      box.appendChild(wrap);
    });

    if ("IntersectionObserver" in window) {
      _pageObserver = new IntersectionObserver(
        (entries) => {
          if (token !== _loadToken) return;
          entries.forEach((entry) => {
            if (!entry.isIntersecting) return;
            const img = entry.target;
            const page = Number(img.parentElement?.dataset.page || 0);
            scheduleWindow(page);
            try { _pageObserver.unobserve(img); } catch (_) {}
          });
        },
        { root: null, rootMargin: ROOT_MARGIN, threshold: 0.01 }
      );
      box.querySelectorAll(".page-slot img[data-src]").forEach((img) => _pageObserver.observe(img));
      // Kick first window immediately
      scheduleWindow(0);
    } else {
      // Fallback: staged native lazy (still not all-at-once)
      box.querySelectorAll("img[data-src]").forEach((img, i) => {
        img.loading = "lazy";
        if (i < PRELOAD) {
          img.src = img.dataset.src;
          img.removeAttribute("data-src");
        }
      });
    }

    const idxs = ctx.state.chapters.map((c) => String(chapterIndex(c)));
    const pos = idxs.indexOf(String(idx));
    const prevBtn = $("#btn-prev-ch");
    const nextBtn = $("#btn-next-ch");
    if (prevBtn) prevBtn.disabled = pos < 0 || pos >= idxs.length - 1;
    if (nextBtn) nextBtn.disabled = pos <= 0;

    // tap zones: list is newest-first → prev chapter = older = +pos, next = newer = -pos
    const tapPrev = $("#tap-prev");
    const tapNext = $("#tap-next");
    if (tapPrev) {
      tapPrev.onclick = (e) => {
        e.preventDefault();
        navChapter(-1);
      };
      tapPrev.disabled = pos < 0 || pos >= idxs.length - 1;
    }
    if (tapNext) {
      tapNext.onclick = (e) => {
        e.preventDefault();
        navChapter(1);
      };
      tapNext.disabled = pos <= 0;
    }
  }

  function navChapter(dir) {
    const idxs = ctx.state.chapters.map((c) => String(chapterIndex(c)));
    const pos = idxs.indexOf(String(ctx.state.chapterIndex));
    if (pos < 0) return;
    const nextPos = pos - dir;
    if (nextPos < 0 || nextPos >= idxs.length) {
      toast(dir > 0 ? "Ini chapter terbaru" : "Ini chapter tertua");
      return;
    }
    openChapter(idxs[nextPos]);
  }

  async function checkHotlink() {
    const images = ctx.state.chapterData?.data?.images || [];
    if (!images.length) {
      toast("Tidak ada halaman untuk diperiksa");
      return;
    }
    const sample = [images[0]];
    if (images.length > 2) sample.push(images[Math.floor(images.length / 2)]);
    if (images.length > 1) sample.push(images[images.length - 1]);

    loading(true);
    try {
      const results = await checkImageStatus(sample);
      renderProbe(results);
    } catch (err) {
      console.error(err);
      toast(String(err.message || err));
    } finally {
      loading(false);
    }
  }

  function renderProbe(results) {
    const panel = $("#hotlink-panel");
    if (!panel) return;
    panel.classList.remove("is-hidden");
    const label = {
      open: "Aman dibaca langsung",
      hotlink_protected: "Perlu proxy agar gambar tampil",
      blocked: "Gambar tidak dapat dimuat",
      mixed: "Hasil campuran",
    };
    panel.innerHTML =
      `<h3>Status gambar (${results.length} sampel)</h3>` +
      results
        .map((res) => {
          const tests = (res.tests || [])
            .map(
              (t) =>
                `<span class="pill ${t.ok_image ? "ok" : "bad"}">${esc(t.strategy)}: ${
                  t.status != null ? t.status : "—"
                }${t.ok_image ? " ✓" : " ✗"}</span>`
            )
            .join("");
          return `<div class="hl-row"><div class="verdict ${escAttr(res.verdict || "")}">${esc(
            label[res.verdict] || res.verdict || ""
          )}</div><div class="hl-tests">${tests}</div></div>`;
        })
        .join("");
  }

  function setPref(key, value) {
    const patch = { [key]: value };
    savePrefs(patch);
    applyPrefs();
  }

  function setMode(mode) {
    savePrefs({ mode: mode || "webtoon" });
    applyPrefs();
  }

  return {
    setWidth,
    setPref,
    openChapter,
    render,
    navChapter,
    checkHotlink,
    reload: render,
    setTheme,
    setFit,
    setMode,
    setPref,
    applyPrefs,
  };
}
