"""SQLite storage layer: connection management and schema.

The catalogue is a single SQLite file.  WAL mode is enabled so the background
scanner thread can write while the UI thread reads.  Each thread gets its own
:class:`Database` instance (and therefore its own connection); SQLite handles
the cross-connection coordination.

The schema is created idempotently and upgraded through a tiny ``PRAGMA
user_version`` migration mechanism, leaving room for future versions.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

SCHEMA_VERSION = 1

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS libraries (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    path         TEXT NOT NULL UNIQUE,
    name         TEXT NOT NULL,
    added_at     TEXT NOT NULL,
    last_scan    TEXT,
    is_available INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS photos (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    library_id    INTEGER NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
    path          TEXT NOT NULL,
    file_name     TEXT NOT NULL,
    ext           TEXT,
    size_bytes    INTEGER,
    mtime         REAL,
    width         INTEGER,
    height        INTEGER,
    date_taken    TEXT,
    camera_make   TEXT,
    camera_model  TEXT,
    lens          TEXT,
    iso           TEXT,
    aperture      TEXT,
    shutter       TEXT,
    focal_length  TEXT,
    instance_id   TEXT,
    content_hash  TEXT,
    thumb_name    TEXT,
    is_missing    INTEGER NOT NULL DEFAULT 0,
    date_added    TEXT NOT NULL,
    date_modified TEXT,
    UNIQUE(library_id, path)
);

CREATE INDEX IF NOT EXISTS idx_photos_library   ON photos(library_id);
CREATE INDEX IF NOT EXISTS idx_photos_date       ON photos(date_taken);
CREATE INDEX IF NOT EXISTS idx_photos_instance   ON photos(instance_id);
CREATE INDEX IF NOT EXISTS idx_photos_hash       ON photos(content_hash);
CREATE INDEX IF NOT EXISTS idx_photos_missing    ON photos(library_id, is_missing);

CREATE TABLE IF NOT EXISTS tag_categories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    color       TEXT NOT NULL DEFAULT '#8a8f98',
    is_builtin  INTEGER NOT NULL DEFAULT 0,
    sort_order  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tags (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL REFERENCES tag_categories(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    color       TEXT NOT NULL DEFAULT '#8a8f98',
    created_at  TEXT NOT NULL,
    UNIQUE(category_id, name)
);

CREATE INDEX IF NOT EXISTS idx_tags_category ON tags(category_id);

CREATE TABLE IF NOT EXISTS photo_tags (
    photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    tag_id   INTEGER NOT NULL REFERENCES tags(id)   ON DELETE CASCADE,
    PRIMARY KEY (photo_id, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_photo_tags_tag ON photo_tags(tag_id);
"""

#: Built-in tag categories, seeded on first run.  Names are localised for the
#: target audience (transport photographers); users can freely add their own.
BUILTIN_CATEGORIES: list[tuple[str, str]] = [
    ("Транспортное средство", "#4f8cff"),
    ("Модель", "#7c5cff"),
    ("Бортовой номер", "#00b3a4"),
    ("Парк / Предприятие", "#ff8a3d"),
    ("Город", "#3ddc84"),
    ("Поездка", "#ff5c8a"),
    ("Дата съёмки", "#c0c0c0"),
    ("Пользовательские теги", "#8a8f98"),
]


class Database:
    """A thread-local connection to the catalogue database."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        # ``check_same_thread=False`` lets us keep one connection per Database
        # instance; the instance itself is never shared between threads.
        self.conn = sqlite3.connect(
            self.path, check_same_thread=False, timeout=10.0
        )
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA synchronous = NORMAL")
        # Wait instead of failing when the scanner thread holds a write lock.
        self.conn.execute("PRAGMA busy_timeout = 10000")
        # SQLite's built-in LOWER/LIKE only fold ASCII; register a Python
        # implementation so Cyrillic search is case-insensitive too.
        self.conn.create_function(
            "py_lower", 1, lambda value: value.lower() if isinstance(value, str) else value
        )
        self._lock = threading.RLock()
        self._create_schema()

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        with self._lock:
            self.conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- low level ---------------------------------------------------------
    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.conn.execute(sql, params)

    def executemany(self, sql: str, seq) -> sqlite3.Cursor:
        with self._lock:
            return self.conn.executemany(sql, seq)

    def commit(self) -> None:
        with self._lock:
            self.conn.commit()

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(sql, params).fetchone()

    # -- schema / migrations ----------------------------------------------
    def _create_schema(self) -> None:
        with self._lock:
            self.conn.executescript(_SCHEMA)
            current = self.conn.execute("PRAGMA user_version").fetchone()[0]
            if current < SCHEMA_VERSION:
                self._migrate(current, SCHEMA_VERSION)
                self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            self.conn.commit()
        self._seed_builtin_categories()

    def _migrate(self, from_version: int, to_version: int) -> None:
        """Apply incremental migrations.

        Add new ``if from_version <= N < to_version`` blocks here as the schema
        evolves.  Migrations must be additive and idempotent-friendly.
        """
        # Version 1 is the initial schema created above — nothing to do.
        return

    def _seed_builtin_categories(self) -> None:
        with self._lock:
            for order, (name, color) in enumerate(BUILTIN_CATEGORIES):
                self.conn.execute(
                    "INSERT OR IGNORE INTO tag_categories(name, color, is_builtin, sort_order)"
                    " VALUES (?, ?, 1, ?)",
                    (name, color, order),
                )
            self.conn.commit()