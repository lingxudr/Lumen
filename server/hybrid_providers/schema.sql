-- =============================================================================
-- Hybrid Manga DB Schema
-- Providers: komikcast, komiku
-- =============================================================================

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- Canonical manga (hasil merger / normalizer)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS manga (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    slug            TEXT NOT NULL UNIQUE,          -- canonical slug (biasanya dari provider utama)
    title           TEXT NOT NULL,
    title_alt       TEXT,
    synopsis        TEXT,
    cover_url       TEXT,
    author          TEXT,
    status          TEXT,                         -- Ongoing | Completed | Hiatus | Unknown
    type            TEXT,                         -- Manga | Manhwa | Manhua | ...
    genres_json     TEXT DEFAULT '[]',            -- JSON array string
    rating          REAL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    last_synced_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_manga_title ON manga(title);
CREATE INDEX IF NOT EXISTS idx_manga_status ON manga(status);
CREATE INDEX IF NOT EXISTS idx_manga_updated ON manga(updated_at);

-- ---------------------------------------------------------------------------
-- Mapping slug per provider → canonical manga
-- Satu manga bisa punya slug berbeda di komikcast vs komiku
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS manga_source (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    manga_id        INTEGER NOT NULL REFERENCES manga(id) ON DELETE CASCADE,
    provider        TEXT NOT NULL,                -- 'komikcast' | 'komiku'
    source_slug     TEXT NOT NULL,
    source_id       TEXT,                         -- id numerik provider (jika ada)
    source_url      TEXT,
    raw_json        TEXT,                         -- snapshot metadata mentah (opsional)
    is_primary      INTEGER NOT NULL DEFAULT 0,   -- 1 = sumber utama metadata
    last_synced_at  TEXT,
    UNIQUE(provider, source_slug)
);

CREATE INDEX IF NOT EXISTS idx_manga_source_manga ON manga_source(manga_id);
CREATE INDEX IF NOT EXISTS idx_manga_source_provider ON manga_source(provider);

-- ---------------------------------------------------------------------------
-- Canonical chapter (satu nomor chapter per manga)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chapter (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    manga_id        INTEGER NOT NULL REFERENCES manga(id) ON DELETE CASCADE,
    number          REAL NOT NULL,                -- 200, 200.5, dll
    name            TEXT,                         -- "Chapter 200"
    published_at    TEXT,                         -- tanggal rilis jika diketahui
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(manga_id, number)
);

CREATE INDEX IF NOT EXISTS idx_chapter_manga ON chapter(manga_id);
CREATE INDEX IF NOT EXISTS idx_chapter_number ON chapter(manga_id, number DESC);

-- ---------------------------------------------------------------------------
-- Source per chapter: provider mana yang punya chapter ini
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chapter_source (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    chapter_id      INTEGER NOT NULL REFERENCES chapter(id) ON DELETE CASCADE,
    provider        TEXT NOT NULL,                -- 'komikcast' | 'komiku'
    source_url      TEXT,                         -- URL halaman chapter di provider
    source_chapter_id TEXT,                       -- id internal provider
    available       INTEGER NOT NULL DEFAULT 1,   -- 0 = pernah gagal / hilang
    priority        INTEGER NOT NULL DEFAULT 100, -- lebih kecil = lebih diutamakan
    last_checked_at TEXT,
    UNIQUE(chapter_id, provider)
);

CREATE INDEX IF NOT EXISTS idx_chapter_source_chapter ON chapter_source(chapter_id);
CREATE INDEX IF NOT EXISTS idx_chapter_source_provider ON chapter_source(provider);

-- ---------------------------------------------------------------------------
-- Cache gambar halaman (hasil get_pages)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS page_cache (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    chapter_id      INTEGER NOT NULL REFERENCES chapter(id) ON DELETE CASCADE,
    provider        TEXT NOT NULL,
    page_index      INTEGER NOT NULL,             -- 0-based urutan baca
    image_url       TEXT NOT NULL,
    width           INTEGER,
    height          INTEGER,
    fetched_at      TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at      TEXT,                         -- opsional TTL
    UNIQUE(chapter_id, provider, page_index)
);

CREATE INDEX IF NOT EXISTS idx_page_cache_chapter ON page_cache(chapter_id, provider);

-- ---------------------------------------------------------------------------
-- Sync / job log (opsional, berguna untuk debug hybrid)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sync_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    provider        TEXT,
    job_type        TEXT,                         -- manga | chapters | pages
    target          TEXT,                         -- slug / chapter number
    status          TEXT,                         -- ok | error | skip
    message         TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
