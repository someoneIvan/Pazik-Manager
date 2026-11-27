"""Plain data objects shared across the application.

Keeping these as lightweight dataclasses (instead of raw sqlite rows) gives the
UI and the core a stable contract and leaves room to grow — e.g. the ``Photo``
object can gain new fields as richer entities (vehicles, depots, ...) are added
later without breaking callers.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Supported formats
# ---------------------------------------------------------------------------

#: Formats the catalogue knows how to index.  ``RAW`` formats are read through
#: ExifTool; the plain raster formats are additionally handled by Pillow.
SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(
    {
        # Raster
        "jpg",
        "jpeg",
        "png",
        "tif",
        "tiff",
        "webp",
        "bmp",
        # RAW (read-only, via ExifTool)
        "cr2",
        "cr3",
        "crw",
        "nef",
        "nrw",
        "arw",
        "srf",
        "sr2",
        "raf",
        "orf",
        "rw2",
        "raw",
        "dng",
        "pef",
        "srw",
        "erf",
        "mos",
        "mrw",
        "x3f",
        "fff",
        "iiq",
    }
)

#: Extensions that Pillow can usually decode directly (used to decide whether a
#: thumbnail can be produced without ExifTool).
RASTER_EXTENSIONS: frozenset[str] = frozenset(
    {"jpg", "jpeg", "png", "tif", "tiff", "webp", "bmp"}
)


def is_supported(filename: str) -> bool:
    """Return ``True`` if *filename* has a supported photo extension."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext in SUPPORTED_EXTENSIONS


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass
class Library:
    id: int
    path: str
    name: str
    added_at: str
    last_scan: Optional[str] = None
    is_available: bool = True
    photo_count: int = 0


@dataclass
class TagCategory:
    id: int
    name: str
    color: str = "#8a8f98"
    is_builtin: bool = False
    sort_order: int = 0


@dataclass
class Tag:
    id: int
    category_id: int
    name: str
    color: str = "#8a8f98"
    photo_count: int = 0


@dataclass
class Photo:
    """A single catalogued photograph."""

    id: int
    library_id: int
    path: str
    file_name: str
    ext: str = ""
    size_bytes: Optional[int] = None
    mtime: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    date_taken: Optional[str] = None
    camera_make: Optional[str] = None
    camera_model: Optional[str] = None
    lens: Optional[str] = None
    iso: Optional[str] = None
    aperture: Optional[str] = None
    shutter: Optional[str] = None
    focal_length: Optional[str] = None
    instance_id: Optional[str] = None
    content_hash: Optional[str] = None
    thumb_name: Optional[str] = None
    is_missing: bool = False
    date_added: Optional[str] = None
    date_modified: Optional[str] = None
    #: Signed decimal degrees (WGS-84), read from EXIF or set manually.
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    #: ``"exif"`` when imported from the file, ``"manual"`` when set in the UI.
    geo_source: Optional[str] = None
    tags: list[Tag] = field(default_factory=list)

    @property
    def dimensions(self) -> str:
        if self.width and self.height:
            return f"{self.width}×{self.height}"
        return ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def now_iso() -> str:
    """Current local time as an ISO-8601 string (seconds resolution)."""
    return _dt.datetime.now().replace(microsecond=0).isoformat()


def normalise_exif_datetime(value: str | None) -> Optional[str]:
    """Convert an EXIF ``YYYY:MM:DD HH:MM:SS`` string to ISO-8601.

    Handles sub-seconds and timezone suffixes (``+03:00`` / ``Z``), which some
    cameras and XMP writers include.  The returned value is a naive local
    timestamp with seconds precision, comparable as a string in SQLite.
    """
    if not value:
        return None
    value = value.strip().replace("\x00", "")
    if not value:
        return None

    # EXIF uses "2024:07:01 13:45:30"; some writers already use "-" or "T".
    # Only rewrite the first two colons when they belong to the *date* part,
    # otherwise an ISO string's time colons would be corrupted.
    if re.match(r"^\d{4}:\d{2}:\d{2}", value):
        value = value.replace(":", "-", 2)
    candidate = value.replace("T", " ", 1)
    candidate = re.sub(r"\.\d+", "", candidate)               # drop sub-seconds
    candidate = re.sub(r"(?:Z|[+-]\d{2}:?\d{2})$", "", candidate)  # drop offset

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return _dt.datetime.strptime(candidate, fmt).replace(microsecond=0).isoformat()
        except ValueError:
            continue
    return None