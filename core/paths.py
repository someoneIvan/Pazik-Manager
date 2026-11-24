"""Application-wide filesystem locations.

All persistent state (the SQLite catalogue, the thumbnail cache, etc.) lives
under a single per-user data directory so the catalogue stays portable and can
be backed up or moved between machines easily.

The location can be overridden with the ``PAZIK_DATA_DIR`` environment
variable, which is mainly useful for the test-suite and for portable installs.
"""

from __future__ import annotations

import os
from pathlib import Path

APP_DIR_NAME = "PazikManager"
DB_FILENAME = "pazik.db"


def data_dir() -> Path:
    """Return the root data directory, creating it if necessary."""
    override = os.environ.get("PAZIK_DATA_DIR")
    if override:
        root = Path(override).expanduser()
    else:
        base = os.environ.get("APPDATA")
        if base:
            root = Path(base) / APP_DIR_NAME
        else:
            # Linux / macOS fallback
            root = Path.home() / ".local" / "share" / APP_DIR_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def database_path() -> Path:
    """Absolute path to the SQLite catalogue file."""
    return data_dir() / DB_FILENAME


def cache_dir() -> Path:
    """Root directory for generated caches (thumbnails, ...)."""
    path = data_dir() / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def thumbnail_cache_dir() -> Path:
    """Directory holding generated thumbnail images."""
    path = cache_dir() / "thumbnails"
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    """Directory holding application log files."""
    path = data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def bundled_exiftool() -> Path | None:
    """Path to an ExifTool executable bundled with the application, if any.

    A bundled ``exiftool.exe`` (Windows) or ``exiftool`` placed in
    ``resources/tools`` next to the project root is picked up automatically.
    """
    candidates = [
        Path(__file__).resolve().parent.parent / "resources" / "tools" / "exiftool.exe",
        Path(__file__).resolve().parent.parent / "resources" / "tools" / "exiftool",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None