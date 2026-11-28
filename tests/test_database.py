"""Tests for the catalogue schema migrator."""

from __future__ import annotations

import sqlite3

from core.database import Database, _SCHEMA


def test_v2_database_gains_geotag_columns(tmp_path):
    """Opening an older catalogue must add the geotag columns in place."""
    db_path = tmp_path / "v2.db"

    # Build the pre-v3 schema by dropping the new column definitions.
    schema = _SCHEMA
    for column in ("    latitude      REAL,\n",
                   "    longitude     REAL,\n",
                   "    geo_source    TEXT,\n"):
        schema = schema.replace(column, "")
    assert "latitude" not in schema

    conn = sqlite3.connect(db_path)
    conn.executescript(schema)
    conn.execute(
        "INSERT INTO libraries(path, name, added_at, is_available) "
        "VALUES ('x', 'x', '2024-01-01', 1)"
    )
    conn.execute(
        "INSERT INTO photos(library_id, path, file_name, date_added) "
        "VALUES (1, 'x/a.jpg', 'a.jpg', '2024-01-01')"
    )
    conn.execute("PRAGMA user_version = 2")
    conn.commit()
    conn.close()

    db = Database(db_path)
    columns = {row[1] for row in db.query("PRAGMA table_info(photos)")}
    version = db.query_one("PRAGMA user_version")[0]
    geo_source = db.query_one("SELECT geo_source FROM photos")["geo_source"]
    db.close()

    assert {"latitude", "longitude", "geo_source"} <= columns
    assert version == 3
    # Existing photos are queued for a one-time EXIF location read.
    assert geo_source == "pending"