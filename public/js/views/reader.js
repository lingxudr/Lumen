import { api, apiPrefetch, proxyImageUrl, checkImageStatus } from "../api.js";
import { $, esc, escAttr, chapterIndex } from "../utils.js";
import { toast, loading, showView, setImg, renderState } from "../ui.js";
import { saveLastRead, getPrefs, savePrefs } from "../storage.js";

export function createReaderView(ctx) {
  let scrollBound = false;
  let lastScrollY = 0;

  function applyPrefs() {
    const prefs = getPrefs();
    const stage = $("#reader-stage") || document.body;
    document.body.dataset.readerTheme = prefs.theme || "dark";
    document.body.dataset.readerFit = prefs.fit || "width";
    document.querySelectorAll(".seg-btn[data-theme]").forEach((b) => {
      b.classList.toggle("is-active", b.dataset.theme === prefs.theme);
    });
    document.querySelectorAll(".seg-btn[data-fit]").forEach((b) => {
      b.classList.toggle("is-active", b.dataset.fit === prefs.fit);
    });
  }

  function setTheme(theme) {
    savePrefs({ theme });
    applyPrefs();
  }

  function setFit(fit) {
    savePrefs({ fit });
    applyPrefs();
  }

  function updateProgress() {
    const bar = $("#reader-progress-bar");
    const wrap = $("#reader-progress");
    if (!bar || !wrap) return;
    const el = document.documentElement;
    const max = el.scrollHeight - el.clientHeight;
    const pct = max > 0 ? Math.min(100, (window.scrollY / max) * 100) : 0;
    bar.style.width = pct + "%";
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
    // list newest-first: pos-1 = newer, pos+1 = older
    for (const p of [pos - 1, pos + 1]) {
      if (p < 0 || p >= idxs.length) continue;
      apiPrefetch(
        `series/${encodeURIComponent(slug)}/chapters/${encodeURIComponent(idxs[p])}`,
        {},
        { ttl: 15 * 60_000, stale: 30 * 60_000 }
      );
    }
  }

  async function openChapter(index) {
    const slug = ctx.state.series?.data?.slug || ctx.state.series?.slug;
    if (!slug) {
      toast("Data judul tidak lengkap");
      return;
    }
    ctx.state.chapterIndex = index;
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
      const msg = String(err.message || err);
      toast(msg);
      showView("reader");
      const box = document.querySelector("#reader-pages");
      if (box) {
        renderState(box, {
          title: "Gagal memuat chapter",
          detail: msg,
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
    $("#reader-title").textContent = `${d.title || ""} · Ch. ${idx}`;

    const images = ch?.data?.images || [];
    const useProxy = $("#use-proxy")?.checked;
    const box = $("#reader-pages");
    box.innerHTML = "";

    if (!images.length) {
      box.innerHTML = '<div class="img-error">Halaman tidak tersedia untuk chapter ini.</div>';
      return;
    }

    // Lazy + preload 2–3 halaman ke depan
    const PRELOAD = 3;
    const resolved = images.map((url) => (useProxy ? proxyImageUrl(url, { webp: true }) : url));

    resolved.forEach((src, i) => {
      const wrap = document.createElement("div");
      wrap.className = "page-slot";
      wrap.dataset.page = String(i);

      const img = document.createElement("img");
      img.alt = `Halaman ${i + 1}`;
      img.decoding = "async";
      img.referrerPolicy = "no-referrer";

      if (i < PRELOAD) {
        // halaman awal + preload buffer
        img.src = src;
        img.loading = i === 0 ? "eager" : "lazy";
      } else {
        img.dataset.src = src;
        img.loading = "lazy";
        // placeholder tinggi biar scroll stabil
        img.classList.add("img-pending");
      }

      img.onerror = () => {
        if (!useProxy && !img.dataset.proxied) {
          img.dataset.proxied = "1";
          const raw = images[i];
          img.src = proxyImageUrl(raw, { webp: false });
          return;
        }
        const div = document.createElement("div");
        div.className = "img-error";
        div.textContent = `Gagal memuat halaman ${i + 1}`;
        wrap.replaceChild(div, img);
      };

      wrap.appendChild(img);
      box.appendChild(wrap);
    });

    // IntersectionObserver: saat mendekati viewport, load + preload berikutnya
    if ("IntersectionObserver" in window) {
      const slots = box.querySelectorAll(".page-slot img[data-src]");
      const io = new IntersectionObserver(
        (entries) => {
          entries.forEach((entry) => {
            if (!entry.isIntersecting) return;
            const img = entry.target;
            const page = Number(img.parentElement?.dataset.page || 0);
            // load current + next PRELOAD
            for (let j = page; j <= page + PRELOAD && j < resolved.length; j++) {
              const el = box.querySelector(`.page-slot[data-page="${j}"] img`);
              if (el && el.dataset.src) {
                el.src = el.dataset.src;
                el.removeAttribute("data-src");
                el.classList.remove("img-pending");
              }
            }
            io.unobserve(img);
          });
        },
        { rootMargin: "600px 0px", threshold: 0.01 }
      );
      slots.forEach((img) => io.observe(img));
    } else {
      // fallback: load all with native lazy
      box.querySelectorAll("img[data-src]").forEach((img) => {
        img.src = img.dataset.src;
        img.removeAttribute("data-src");
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

  return {
    openChapter,
    render,
    navChapter,
    checkHotlink,
    reload: render,
    setTheme,
    setFit,
    applyPrefs,
  };
}
