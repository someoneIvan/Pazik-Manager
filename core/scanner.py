"""Recursive library scanner.

Walks a library directory tree, discovers supported photos, reads their
metadata, generates thumbnails and records everything in the catalogue — all
without touching the original files.

The scanner is designed to run on a background thread.  The caller creates a
:class:`~core.catalog.Catalog` (and therefore a SQLite connection) for the
worker thread and passes it in; progress is reported through a callback and the
work can be cancelled cooperatively.

Performance notes:

* Files whose size and mtime are unchanged are skipped entirely, so rescanning a
  large library is cheap and RAW previews are not decoded again.
* Metadata is only rewritten when a file changed or its metadata is missing.
* Rows are committed in batches, which keeps the UI's connection responsive.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import exiftool, identification, metadata, thumbnail
from .catalog import Catalog
from .models import RASTER_EXTENSIONS, Library, is_supported

log = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int, str], None]  # (done, total, current_path)
CancelCheck = Callable[[], bool]

#: Directories that never contain user photos and slow scans down.
SKIP_DIRS = {
    "$RECYCLE.BIN",
    "System Volume Information",
    "@eaDir",            # Synology thumbnails
    ".thumbnails",
    ".git",
    "__pycache__",
}

#: Commit accumulated rows every N files.
COMMIT_EVERY = 200


@dataclass
class ScanResult:
    library_id: int
    found: int = 0
    added: int = 0
    updated: int = 0
    skipped: int = 0
    relocated: int = 0
    missing: int = 0
    errors: int = 0
    cancelled: bool = False
    duration: float = 0.0
    error_messages: list[str] = field(default_factory=list)


class Scanner:
    def __init__(
        self,
        catalog: Catalog,
        progress: Optional[ProgressCallback] = None,
        cancel_check: Optional[CancelCheck] = None,
    ):
        self.catalog = catalog
        self.progress = progress
        self.cancel_check = cancel_check

    # -- public API --------------------------------------------------------
    def scan_library(
        self,
        library: Library,
        make_thumbnails: bool = True,
        regenerate_thumbnails: bool = False,
    ) -> ScanResult:
        result = ScanResult(library_id=library.id)
        start = time.perf_counter()
        root = Path(library.path)

        if not root.exists():
            self.catalog.set_library_availability(library.id, False)
            result.errors += 1
            result.error_messages.append(f"Каталог недоступен: {root}")
            result.duration = time.perf_counter() - start
            return result

        self.catalog.set_library_availability(library.id, True)

        files, walk_errors = self._discover(root)
        result.found = len(files)
        result.errors += len(walk_errors)
        result.error_messages.extend(walk_errors)
        total = len(files)
        self._report(0, total, "")

        # The full set of paths present in this scan.  Computed up-front so the
        # relocation logic can tell a moved file (old path absent here) from a
        # still-in-place one, within the same pass — before ``mark_missing`` runs
        # at the end and flags the old row.
        present_paths: set[str] = {str(path) for path in files}

        for index, path in enumerate(files, start=1):
            if self._cancelled():
                result.cancelled = True
                break

            self._report(index, total, str(path))
            try:
                self._process_file(
                    library,
                    path,
                    result,
                    make_thumbnails=make_thumbnails,
                    regenerate_thumbnails=regenerate_thumbnails,
                    present_paths=present_paths,
                )
            except Exception as exc:  # pragma: no cover - defensive
                result.errors += 1
                result.error_messages.append(f"{path}: {exc}")
                log.exception("Failed to process %s", path)

            if index % COMMIT_EVERY == 0:
                self.catalog.commit()

        self.catalog.commit()

        # Only reconcile missing files after a *complete* scan: a cancelled scan
        # must never flag thousands of photos as missing.
        if not result.cancelled:
            result.missing = self.catalog.mark_missing(library.id, present_paths)
            self.catalog.touch_library_scan(library.id)

        result.duration = time.perf_counter() - start
        self._report(total, total, "")
        return result

    # -- discovery ---------------------------------------------------------
    def _discover(self, root: Path) -> tuple[list[Path], list[str]]:
        found: list[Path] = []
        errors: list[str] = []

        def on_error(exc: OSError) -> None:
            errors.append(str(exc))

        for dirpath, dirnames, filenames in os.walk(root, onerror=on_error):
            dirnames[:] = [
                d
                for d in dirnames
                if not d.startswith(".") and d not in SKIP_DIRS
            ]
            for name in filenames:
                if name.startswith("."):
                    continue
                if is_supported(name):
                    found.append(Path(dirpath) / name)

        found.sort(key=lambda p: str(p).lower())
        return found, errors

    # -- per-file ----------------------------------------------------------
    def _process_file(
        self,
        library: Library,
        path: Path,
        result: ScanResult,
        make_thumbnails: bool,
        regenerate_thumbnails: bool,
        present_paths: set[str],
    ) -> None:
        signature = identification.file_signature(path)
        if signature is None:
            return
        size, mtime = signature

        row = self.catalog.db.query_one(
            "SELECT id, size_bytes, mtime, thumb_name, width, date_taken, "
            "content_hash, is_missing, geo_source FROM photos "
            "WHERE library_id = ? AND path = ?",
            (library.id, str(path)),
        )

        if row is not None:
            photo_id = int(row["id"])
            changed = self._content_changed(row, size, mtime)
            # Retry metadata extraction only when it can plausibly succeed:
            # raster files via Pillow, RAW only when ExifTool is installed.
            ext = path.suffix.lower().lstrip(".")
            can_read_metadata = ext in RASTER_EXTENSIONS or exiftool.is_available()
            needs_metadata = row["width"] is None and can_read_metadata

            if not changed and not needs_metadata and not regenerate_thumbnails:
                # Unchanged file: do not touch metadata or the thumbnail.
                if row["is_missing"]:
                    self.catalog.mark_present(photo_id)
                    result.updated += 1
                else:
                    result.skipped += 1
                return
            result.updated += 1
        else:
            # Unknown path: perhaps the file was moved since the last scan.
            photo_id = self._maybe_relocate(
                library, path, size, result, present_paths
            )
            changed = True

        fields: dict = {"size_bytes": size, "mtime": mtime}
        meta = self._read_metadata(path)

        # Geotags: EXIF coordinates are imported on first sight, but a value set
        # manually in the UI is never overwritten by a later rescan.
        geo_latitude = meta.pop("latitude", None)
        geo_longitude = meta.pop("longitude", None)
        fields.update(meta)

        if geo_latitude is not None and geo_longitude is not None:
            geo_source = row["geo_source"] if row is not None else None
            if geo_source is None and photo_id is not None:
                existing = self.catalog.db.query_one(
                    "SELECT geo_source FROM photos WHERE id = ?", (photo_id,)
                )
                geo_source = existing["geo_source"] if existing else None
            if geo_source != "manual":
                fields["latitude"] = geo_latitude
                fields["longitude"] = geo_longitude
                fields["geo_source"] = "exif"

        if photo_id is None:
            # New photo: fingerprint it and render the first thumbnail.
            fields["content_hash"] = identification.partial_hash(path)
            if make_thumbnails:
                thumb = thumbnail.get_thumbnail(path, size, mtime)
                if thumb:
                    fields["thumb_name"] = thumb.name
            self.catalog.upsert_photo(library.id, str(path), **fields)
            result.added += 1
            return

        # Existing (or relocated) row that needs refreshing.
        if changed or not row or not row["content_hash"]:
            fields["content_hash"] = identification.partial_hash(path)

        if make_thumbnails and (
            regenerate_thumbnails or changed or not (row and row["thumb_name"])
        ):
            thumb = thumbnail.get_thumbnail(
                path, size, mtime, force=regenerate_thumbnails or changed
            )
            if thumb:
                fields["thumb_name"] = thumb.name

        self.catalog.upsert_photo(library.id, str(path), **fields)

    def _read_metadata(self, path: Path) -> dict:
        """Extract metadata, never letting a bad file abort the scan."""
        try:
            return metadata.extract(path).non_empty()
        except Exception as exc:  # pragma: no cover - defensive
            log.debug("Metadata extraction failed for %s: %s", path, exc)
            return {}

    def _maybe_relocate(
        self, library: Library, path: Path, size: int, result: ScanResult,
        present_paths: set[str],
    ) -> Optional[int]:
        """Reconnect a moved file to its existing row so its tags survive.

        A candidate is any row in this library whose path is *not* among the
        files seen in the current scan (i.e. the file left its old location).
        The cheap name+size pre-filter runs before the hash is computed.
        """
        rows = self.catalog.db.query(
            "SELECT id, path FROM photos "
            "WHERE library_id = ? AND file_name = ? AND size_bytes = ?",
            (library.id, path.name, size),
        )
        if not any(row["path"] not in present_paths for row in rows):
            return None

        content_hash = identification.partial_hash(path)
        new_id = self.catalog.find_relocation(
            library.id,
            instance_id=None,
            file_name=path.name,
            size=size,
            content_hash=content_hash,
            present_paths=present_paths,
        )
        if new_id is not None:
            self.catalog.update_photo_path(new_id, str(path))
            result.relocated += 1
            return new_id
        return None

    @staticmethod
    def _content_changed(row, size: int, mtime: float) -> bool:
        if row["size_bytes"] != size:
            return True
        return abs((row["mtime"] or 0) - mtime) > 1.0

    # -- helpers -----------------------------------------------------------
    def _cancelled(self) -> bool:
        return bool(self.cancel_check and self.cancel_check())

    def _report(self, done: int, total: int, current: str) -> None:
        if self.progress:
            try:
                self.progress(done, total, current)
            except Exception:  # pragma: no cover - never let UI errors break scan
                log.exception("Progress callback failed")