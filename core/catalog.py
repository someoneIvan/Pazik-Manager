"""High-level catalogue operations.

:class:`Catalog` is the single entry point the UI (and the scanner) use to talk
to storage.  It wraps :class:`core.database.Database` with friendly methods for
libraries, photos, tags and search, and returns :mod:`core.models` dataclasses.

All methods are safe to call from the worker thread that runs a scan: create a
separate ``Catalog`` instance per thread (each gets its own SQLite connection).
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from . import paths
from .database import Database
from .geo import is_valid
from .models import (
    Library,
    Photo,
    Tag,
    TagCategory,
    is_supported,
    now_iso,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Query description
# ---------------------------------------------------------------------------


@dataclass
class PhotoQuery:
    """Declarative description of a gallery query / filter set."""

    library_id: Optional[int] = None
    text: Optional[str] = None
    tag_ids: list[int] = field(default_factory=list)
    tag_match: str = "any"  # "any" or "all"
    untagged: bool = False
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    camera_model: Optional[str] = None
    missing: Optional[bool] = None  # None = ignore, True/False = filter
    limit: Optional[int] = None
    offset: int = 0


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


class Catalog:
    def __init__(self, db_path: str | Path | None = None):
        self.db = Database(db_path or paths.database_path())

    def close(self) -> None:
        self.db.close()

    # -- libraries ---------------------------------------------------------
    def list_libraries(self) -> list[Library]:
        rows = self.db.query(
            "SELECT l.*, "
            "  (SELECT COUNT(*) FROM photos p WHERE p.library_id = l.id) AS photo_count "
            "FROM libraries l ORDER BY l.added_at"
        )
        return [
            Library(
                id=r["id"],
                path=r["path"],
                name=r["name"],
                added_at=r["added_at"],
                last_scan=r["last_scan"],
                is_available=bool(r["is_available"]),
                photo_count=r["photo_count"],
            )
            for r in rows
        ]

    def get_library(self, library_id: int) -> Optional[Library]:
        r = self.db.query_one("SELECT * FROM libraries WHERE id = ?", (library_id,))
        if not r:
            return None
        return Library(
            id=r["id"],
            path=r["path"],
            name=r["name"],
            added_at=r["added_at"],
            last_scan=r["last_scan"],
            is_available=bool(r["is_available"]),
        )

    def find_library_by_path(self, path: str | Path) -> Optional[Library]:
        key = str(Path(path).resolve())
        # Paths are compared case-insensitively so the same folder is not added
        # twice because of drive-letter / case differences.
        r = self.db.query_one(
            "SELECT * FROM libraries WHERE py_lower(path) = ?", (key.lower(),)
        )
        if not r:
            return None
        return Library(
            id=r["id"],
            path=r["path"],
            name=r["name"],
            added_at=r["added_at"],
            last_scan=r["last_scan"],
            is_available=bool(r["is_available"]),
        )

    def add_library(self, path: str | Path) -> Library:
        p = Path(path).resolve()
        existing = self.find_library_by_path(p)
        if existing:
            return existing
        cur = self.db.execute(
            "INSERT INTO libraries(path, name, added_at, is_available) VALUES (?, ?, ?, 1)",
            (str(p), p.name or str(p), now_iso()),
        )
        self.db.commit()
        return self.get_library(int(cur.lastrowid))  # type: ignore[return-value]

    def find_overlapping_library(self, path: str | Path) -> Optional[Library]:
        """Return an existing library that contains *path* or is inside it.

        Adding a folder that overlaps an existing library would index the same
        files twice, so the UI refuses it.
        """
        target = Path(path).resolve()
        for library in self.list_libraries():
            root = Path(library.path)
            try:
                if target == root:
                    continue
                if target.is_relative_to(root) or root.is_relative_to(target):
                    return library
            except (OSError, ValueError):
                continue
        return None

    def remove_library(self, library_id: int) -> None:
        self.db.execute("DELETE FROM libraries WHERE id = ?", (library_id,))
        self.db.commit()

    def set_library_availability(self, library_id: int, available: bool) -> None:
        self.db.execute(
            "UPDATE libraries SET is_available = ? WHERE id = ?",
            (1 if available else 0, library_id),
        )
        self.db.commit()

    def relocate_library(self, library_id: int, new_root: str | Path) -> int:
        """Point an existing library at a new folder, remapping photo paths.

        This is the "drive letter changed" case: the folder moved but its
        contents are the same.  Photo rows keep their ids (and therefore their
        tags); only the stored paths are rewritten.  Returns the number of
        photos remapped.
        """
        library = self.get_library(library_id)
        if library is None:
            return 0

        old_root = Path(library.path)
        target = Path(new_root).resolve()
        rows = self.db.query(
            "SELECT id, path FROM photos WHERE library_id = ?", (library_id,)
        )

        remapped = 0
        for row in rows:
            try:
                relative = Path(row["path"]).relative_to(old_root)
            except ValueError:
                continue
            new_path = target / relative
            self.db.execute(
                "UPDATE photos SET path = ?, is_missing = 0 WHERE id = ?",
                (str(new_path), row["id"]),
            )
            remapped += 1

        self.db.execute(
            "UPDATE libraries SET path = ?, name = ?, is_available = 1 WHERE id = ?",
            (str(target), target.name or str(target), library_id),
        )
        self.db.commit()
        return remapped

    def touch_library_scan(self, library_id: int) -> None:
        self.db.execute(
            "UPDATE libraries SET last_scan = ?, is_available = 1 WHERE id = ?",
            (now_iso(), library_id),
        )
        self.db.commit()

    # -- photos ------------------------------------------------------------
    def upsert_photo(self, library_id: int, path: str, **fields) -> int:
        """Insert or update a photo row keyed by ``(library_id, path)``.

        Returns the photo id.  Only the supplied *fields* are written on update,
        so a rescan that only re-checks presence does not clobber metadata.
        """
        path = str(path)
        file_name = Path(path).name
        ext = Path(path).suffix.lower().lstrip(".")

        existing = self.db.query_one(
            "SELECT id FROM photos WHERE library_id = ? AND path = ?",
            (library_id, path),
        )
        if existing:
            photo_id = int(existing["id"])
            if fields:
                cols = ", ".join(f"{k} = ?" for k in fields)
                self.db.execute(
                    f"UPDATE photos SET {cols} WHERE id = ?",
                    (*fields.values(), photo_id),
                )
            # A path we just saw is, by definition, not missing.
            self.db.execute(
                "UPDATE photos SET is_missing = 0 WHERE id = ?", (photo_id,)
            )
            return photo_id

        data = {
            "file_name": file_name,
            "ext": ext,
            "date_added": now_iso(),
            "is_missing": 0,
        }
        data.update(fields)
        columns = ", ".join(data)
        placeholders = ", ".join("?" for _ in data)
        cur = self.db.execute(
            f"INSERT INTO photos(library_id, path, {columns}) "
            f"VALUES (?, ?, {placeholders})",
            (library_id, path, *data.values()),
        )
        return int(cur.lastrowid)

    def commit(self) -> None:
        self.db.commit()

    def get_photo(self, photo_id: int) -> Optional[Photo]:
        r = self.db.query_one("SELECT * FROM photos WHERE id = ?", (photo_id,))
        if not r:
            return None
        photo = _row_to_photo(r)
        photo.tags = self.get_photo_tags(photo_id)
        return photo

    def count_photos(self, library_id: int | None = None) -> int:
        if library_id is None:
            r = self.db.query_one("SELECT COUNT(*) AS c FROM photos")
        else:
            r = self.db.query_one(
                "SELECT COUNT(*) AS c FROM photos WHERE library_id = ?", (library_id,)
            )
        return int(r["c"]) if r else 0

    def search(self, query: PhotoQuery) -> list[Photo]:
        """Return photos matching *query*, ordered by shooting date then name."""
        where: list[str] = []
        params: list = []

        if query.library_id is not None:
            where.append("p.library_id = ?")
            params.append(query.library_id)

        if query.text:
            needle = f"%{query.text.strip().lower()}%"
            where.append(
                "("
                "py_lower(p.file_name) LIKE ? OR py_lower(p.path) LIKE ? OR "
                "py_lower(p.camera_model) LIKE ? OR py_lower(p.lens) LIKE ? OR "
                "EXISTS (SELECT 1 FROM photo_tags pt JOIN tags t ON t.id = pt.tag_id "
                "WHERE pt.photo_id = p.id AND py_lower(t.name) LIKE ?)"
                ")"
            )
            params.extend([needle, needle, needle, needle, needle])

        if query.date_from:
            where.append("p.date_taken >= ?")
            params.append(query.date_from)
        if query.date_to:
            where.append("p.date_taken <= ?")
            params.append(query.date_to + "T23:59:59")

        if query.camera_model:
            where.append("p.camera_model LIKE ?")
            params.append(f"%{query.camera_model}%")

        if query.missing is not None:
            where.append("p.is_missing = ?")
            params.append(1 if query.missing else 0)

        if query.untagged:
            where.append(
                "NOT EXISTS (SELECT 1 FROM photo_tags pt WHERE pt.photo_id = p.id)"
            )

        if query.tag_ids:
            placeholders = ", ".join("?" for _ in query.tag_ids)
            if query.tag_match == "all":
                # Every requested tag must be present.
                for tid in query.tag_ids:
                    where.append(
                        "EXISTS (SELECT 1 FROM photo_tags pt WHERE pt.photo_id = p.id "
                        "AND pt.tag_id = ?)"
                    )
                    params.append(tid)
            else:  # any
                where.append(
                    f"p.id IN (SELECT photo_id FROM photo_tags WHERE tag_id IN ({placeholders}))"
                )
                params.extend(query.tag_ids)

        sql = "SELECT p.* FROM photos p"
        if where:
            sql += " WHERE " + " AND ".join(where)
        # Newest photos first; undated files go to the end.
        sql += " ORDER BY (p.date_taken IS NULL), p.date_taken DESC, p.file_name"

        sql += " LIMIT ? OFFSET ?" if query.limit is not None else " LIMIT -1 OFFSET ?"
        if query.limit is not None:
            params.extend([query.limit, query.offset])
        else:
            params.append(query.offset)

        rows = self.db.query(sql, tuple(params))
        return [_row_to_photo(r) for r in rows]

    def iter_all_photos(self, library_id: int) -> list[sqlite3.Row]:
        return self.db.query(
            "SELECT * FROM photos WHERE library_id = ?", (library_id,)
        )

    # -- missing files / relocation ---------------------------------------
    def mark_missing(self, library_id: int, present_paths: set[str]) -> int:
        """Mark photos in *library_id* whose path is not in *present_paths*.

        Returns the number of newly-missing photos.  Used after a scan to flag
        files that disappeared (e.g. an unplugged drive) without deleting them.
        """
        rows = self.db.query(
            "SELECT id, path, is_missing FROM photos WHERE library_id = ?", (library_id,)
        )
        newly = 0
        for r in rows:
            if r["path"] not in present_paths and not r["is_missing"]:
                self.db.execute(
                    "UPDATE photos SET is_missing = 1 WHERE id = ?", (r["id"],)
                )
                newly += 1
        return newly

    def find_relocation(
        self, library_id: int, *, instance_id: str | None, file_name: str,
        size: int | None, content_hash: str | None,
        present_paths: set[str] | None = None,
    ) -> int | None:
        """Try to match a moved file to an existing photo row.

        Strategy, cheapest first: InstanceID → partial content hash → name+size.
        Returns the matching photo id or ``None``.

        A row is eligible when it is *missing* — or, if *present_paths* is
        given, when its path is **not** among the paths seen in the current
        scan.  The latter lets a file that was moved since the last scan be
        reconnected in the *same* pass, before ``mark_missing`` has had a chance
        to flag the old row (which only happens at the end of a scan).
        """

        def eligible(row: sqlite3.Row) -> bool:
            if present_paths is None:
                return bool(row["is_missing"])
            return row["path"] not in present_paths

        def first_match(sql: str, params: tuple) -> int | None:
            for row in self.db.query(sql, params):
                if eligible(row):
                    return int(row["id"])
            return None

        if instance_id:
            found = first_match(
                "SELECT id, path, is_missing FROM photos "
                "WHERE library_id = ? AND instance_id = ?",
                (library_id, instance_id),
            )
            if found is not None:
                return found
        if content_hash:
            found = first_match(
                "SELECT id, path, is_missing FROM photos "
                "WHERE library_id = ? AND content_hash = ?",
                (library_id, content_hash),
            )
            if found is not None:
                return found
        if size is not None:
            found = first_match(
                "SELECT id, path, is_missing FROM photos "
                "WHERE library_id = ? AND file_name = ? AND size_bytes = ?",
                (library_id, file_name, size),
            )
            if found is not None:
                return found
        return None

    def merge_duplicate_photos(self) -> int:
        """Merge photo rows that point at the same image (same content hash).

        Repairs damage left by the historical "move loses the file" bug: a
        moved file ended up as a fresh row (no tags) *plus* the original row
        flagged missing (still carrying the tags).  For every group of rows that
        share a ``content_hash`` we keep a single survivor — preferring a
        present row, then the one with the most tags — move the union of all
        tags onto it, fill any missing metadata from its siblings, and delete the
        extras.  Returns the number of rows removed.

        Safe to run repeatedly: with no duplicates it is a no-op.
        """
        rows = self.db.query(
            "SELECT id, library_id, path, is_missing, content_hash, "
            "width, height, date_taken, camera_make, camera_model, lens, "
            "iso, aperture, shutter, focal_length, latitude, longitude, geo_source "
            "FROM photos WHERE content_hash IS NOT NULL ORDER BY id"
        )
        groups: dict[tuple[int, str], list[sqlite3.Row]] = {}
        for r in rows:
            groups.setdefault((r["library_id"], r["content_hash"]), []).append(r)

        # Geotags are handled separately below: they move as one unit.
        meta_fields = [
            "width", "height", "date_taken", "camera_make", "camera_model",
            "lens", "iso", "aperture", "shutter", "focal_length",
        ]

        removed = 0
        for group in groups.values():
            if len(group) < 2:
                continue

            def rank(r: sqlite3.Row) -> tuple:
                tag_count = self.db.query_one(
                    "SELECT COUNT(*) AS c FROM photo_tags WHERE photo_id = ?",
                    (r["id"],),
                )["c"]
                # Present first, then richer in tags, then lowest id.
                return (1 if not r["is_missing"] else 0, tag_count, -r["id"])

            survivor = max(group, key=rank)
            keep_id = int(survivor["id"])

            # Union of every tag across the group.
            tag_ids: set[int] = set()
            for r in group:
                for t in self.db.query(
                    "SELECT tag_id FROM photo_tags WHERE photo_id = ?", (r["id"],)
                ):
                    tag_ids.add(int(t["tag_id"]))

            # Fill null metadata on the survivor from any sibling that has it.
            updates: dict[str, object] = {}
            for column in meta_fields:
                if survivor[column] is None:
                    for r in group:
                        if r[column] is not None:
                            updates[column] = r[column]
                            break

            # Take a sibling's location only when the survivor has no decision of
            # its own.  A location the user cleared must not come back.
            if survivor["geo_source"] in (None, "pending", "none"):
                donors = [
                    r for r in group
                    if r["latitude"] is not None and r["longitude"] is not None
                ]
                if donors:
                    donor = max(donors, key=lambda r: r["geo_source"] == "manual")
                    updates.update(
                        latitude=donor["latitude"],
                        longitude=donor["longitude"],
                        geo_source=donor["geo_source"],
                    )
            if updates:
                cols = ", ".join(f"{k} = ?" for k in updates)
                self.db.execute(
                    f"UPDATE photos SET {cols} WHERE id = ?",
                    (*updates.values(), keep_id),
                )

            # Drop the non-survivors; their photo_tags rows cascade away.
            for r in group:
                if int(r["id"]) != keep_id:
                    self.db.execute("DELETE FROM photos WHERE id = ?", (r["id"],))
                    removed += 1

            if tag_ids:
                self.db.executemany(
                    "INSERT OR IGNORE INTO photo_tags(photo_id, tag_id) VALUES (?, ?)",
                    [(keep_id, tid) for tid in tag_ids],
                )
            # Only clear "missing" if the image is actually present somewhere.
            if any(not r["is_missing"] for r in group):
                self.db.execute(
                    "UPDATE photos SET is_missing = 0 WHERE id = ?", (keep_id,)
                )

        if removed:
            self.db.commit()
        return removed

    def mark_present(self, photo_id: int) -> None:
        """Clear the ``is_missing`` flag for a photo that was just seen."""
        self.db.execute(
            "UPDATE photos SET is_missing = 0 WHERE id = ? AND is_missing = 1",
            (photo_id,),
        )

    # -- geotags -----------------------------------------------------------
    def set_photo_location(
        self, photo_id: int, latitude: float, longitude: float
    ) -> None:
        """Store manually-set coordinates for a photo.

        The ``manual`` source tells the scanner not to overwrite the values when
        the file's EXIF GPS data is re-read on a later scan.
        """
        latitude, longitude = float(latitude), float(longitude)
        if not is_valid(latitude, longitude):
            raise ValueError(
                f"invalid coordinates: {latitude}, {longitude}"
            )
        self.db.execute(
            "UPDATE photos SET latitude = ?, longitude = ?, geo_source = 'manual' "
            "WHERE id = ?",
            (latitude, longitude, photo_id),
        )
        self.db.commit()

    def clear_photo_location(self, photo_id: int) -> None:
        """Remove a photo's geotag (and keep it removed across rescans)."""
        self.db.execute(
            "UPDATE photos SET latitude = NULL, longitude = NULL, "
            "geo_source = 'manual' WHERE id = ?",
            (photo_id,),
        )
        self.db.commit()

    def update_photo_path(self, photo_id: int, new_path: str) -> None:
        self.db.execute(
            "UPDATE photos SET path = ?, file_name = ?, is_missing = 0 WHERE id = ?",
            (str(new_path), Path(new_path).name, photo_id),
        )
        self.db.commit()

    # -- tag categories ----------------------------------------------------
    def list_categories(self) -> list[TagCategory]:
        rows = self.db.query(
            "SELECT * FROM tag_categories ORDER BY sort_order, name"
        )
        return [
            TagCategory(
                id=r["id"],
                name=r["name"],
                color=r["color"],
                is_builtin=bool(r["is_builtin"]),
                sort_order=r["sort_order"],
            )
            for r in rows
        ]

    def create_category(self, name: str, color: str = "#8a8f98") -> TagCategory:
        name = name.strip()
        existing = self.db.query_one(
            "SELECT * FROM tag_categories WHERE py_lower(name) = ?", (name.lower(),)
        )
        if existing:
            return TagCategory(
                id=existing["id"],
                name=existing["name"],
                color=existing["color"],
                is_builtin=bool(existing["is_builtin"]),
                sort_order=existing["sort_order"],
            )
        cur = self.db.execute(
            "INSERT INTO tag_categories(name, color, is_builtin, sort_order) "
            "VALUES (?, ?, 0, (SELECT COALESCE(MAX(sort_order), 0) + 1 FROM tag_categories))",
            (name, color),
        )
        self.db.commit()
        return TagCategory(id=int(cur.lastrowid), name=name, color=color)

    def rename_category(self, category_id: int, new_name: str) -> None:
        self.db.execute(
            "UPDATE tag_categories SET name = ? WHERE id = ?",
            (new_name.strip(), category_id),
        )
        self.db.commit()

    def delete_category(self, category_id: int) -> None:
        # Tags in the category are removed via ON DELETE CASCADE.
        self.db.execute("DELETE FROM tag_categories WHERE id = ?", (category_id,))
        self.db.commit()

    # -- tags --------------------------------------------------------------
    def list_tags(self, category_id: int | None = None) -> list[Tag]:
        if category_id is None:
            rows = self.db.query(
                "SELECT t.*, "
                "  (SELECT COUNT(*) FROM photo_tags pt WHERE pt.tag_id = t.id) AS photo_count "
                "FROM tags t ORDER BY t.name"
            )
        else:
            rows = self.db.query(
                "SELECT t.*, "
                "  (SELECT COUNT(*) FROM photo_tags pt WHERE pt.tag_id = t.id) AS photo_count "
                "FROM tags t WHERE t.category_id = ? ORDER BY t.name",
                (category_id,),
            )
        return [
            Tag(
                id=r["id"],
                category_id=r["category_id"],
                name=r["name"],
                color=r["color"],
                photo_count=r["photo_count"],
            )
            for r in rows
        ]

    def create_tag(self, name: str, category_id: int, color: str | None = None) -> Tag:
        name = name.strip()
        if color is None:
            r = self.db.query_one(
                "SELECT color FROM tag_categories WHERE id = ?", (category_id,)
            )
            color = r["color"] if r else "#8a8f98"
        # Reuse an existing tag with the same name in the category, if any.
        existing = self.db.query_one(
            "SELECT id FROM tags WHERE category_id = ? AND py_lower(name) = ?",
            (category_id, name.lower()),
        )
        if existing:
            return self.get_tag(int(existing["id"]))
        cur = self.db.execute(
            "INSERT INTO tags(category_id, name, color, created_at) VALUES (?, ?, ?, ?)",
            (category_id, name, color, now_iso()),
        )
        self.db.commit()
        return self.get_tag(int(cur.lastrowid))

    def rename_tag(self, tag_id: int, new_name: str) -> None:
        self.db.execute(
            "UPDATE tags SET name = ? WHERE id = ?", (new_name.strip(), tag_id)
        )
        self.db.commit()

    def delete_tag(self, tag_id: int) -> None:
        self.db.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
        self.db.commit()

    def get_tag(self, tag_id: int) -> Tag:
        r = self.db.query_one("SELECT * FROM tags WHERE id = ?", (tag_id,))
        if r is None:
            raise KeyError(f"tag {tag_id} not found")
        return Tag(
            id=r["id"],
            category_id=r["category_id"],
            name=r["name"],
            color=r["color"],
        )

    # Backwards-compatible private alias used by older UI code.
    def _get_tag(self, tag_id: int) -> Tag:
        return self.get_tag(tag_id)

    # -- photo <-> tag -----------------------------------------------------
    def get_photo_tags(self, photo_id: int) -> list[Tag]:
        rows = self.db.query(
            "SELECT t.* FROM tags t "
            "JOIN photo_tags pt ON pt.tag_id = t.id "
            "WHERE pt.photo_id = ? ORDER BY t.name",
            (photo_id,),
        )
        return [
            Tag(id=r["id"], category_id=r["category_id"], name=r["name"], color=r["color"])
            for r in rows
        ]

    def set_photo_tags(self, photo_id: int, tag_ids: Iterable[int]) -> None:
        """Replace the full tag set of a photo with *tag_ids*."""
        tag_ids = list(tag_ids)
        self.db.execute("DELETE FROM photo_tags WHERE photo_id = ?", (photo_id,))
        if tag_ids:
            self.db.executemany(
                "INSERT OR IGNORE INTO photo_tags(photo_id, tag_id) VALUES (?, ?)",
                [(photo_id, tid) for tid in tag_ids],
            )
        self.db.commit()

    def add_tag_to_photo(self, photo_id: int, tag_id: int) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO photo_tags(photo_id, tag_id) VALUES (?, ?)",
            (photo_id, tag_id),
        )
        self.db.commit()

    def remove_tag_from_photo(self, photo_id: int, tag_id: int) -> None:
        self.db.execute(
            "DELETE FROM photo_tags WHERE photo_id = ? AND tag_id = ?",
            (photo_id, tag_id),
        )
        self.db.commit()

    def distinct_camera_models(self) -> list[str]:
        rows = self.db.query(
            "SELECT DISTINCT camera_model FROM photos "
            "WHERE camera_model IS NOT NULL AND camera_model != '' "
            "ORDER BY camera_model"
        )
        return [r["camera_model"] for r in rows]

    def photo_ids_for_tag(self, tag_id: int) -> list[int]:
        rows = self.db.query(
            "SELECT photo_id FROM photo_tags WHERE tag_id = ?", (tag_id,)
        )
        return [int(r["photo_id"]) for r in rows]


# sqlite3.Row is only referenced for typing.


def _row_to_photo(r: sqlite3.Row) -> Photo:
    return Photo(
        id=r["id"],
        library_id=r["library_id"],
        path=r["path"],
        file_name=r["file_name"],
        ext=r["ext"] or "",
        size_bytes=r["size_bytes"],
        mtime=r["mtime"],
        width=r["width"],
        height=r["height"],
        date_taken=r["date_taken"],
        camera_make=r["camera_make"],
        camera_model=r["camera_model"],
        lens=r["lens"],
        iso=r["iso"],
        aperture=r["aperture"],
        shutter=r["shutter"],
        focal_length=r["focal_length"],
        instance_id=r["instance_id"],
        content_hash=r["content_hash"],
        thumb_name=r["thumb_name"],
        is_missing=bool(r["is_missing"]),
        date_added=r["date_added"],
        date_modified=r["date_modified"],
        latitude=r["latitude"],
        longitude=r["longitude"],
        geo_source=r["geo_source"],
    )


__all__ = ["Catalog", "PhotoQuery", "is_supported"]