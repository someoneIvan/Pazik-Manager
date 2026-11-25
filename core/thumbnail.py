"""Thumbnail generation and caching.

Thumbnails are rendered on a fixed 4:3 canvas and stored as JPEG files in the
per-user cache directory.  The cache file name is derived from the source path +
size + mtime, so editing/replacing a photo invalidates its thumbnail
automatically while untouched photos reuse the cached version across scans.

Every thumbnail has exactly the same pixel size, which keeps the gallery a tidy
grid of 4:3 rectangles.  Images that are not 4:3 are scaled to cover the canvas
and their overflowing edges are cropped (centred), so nothing is distorted.

RAW files (CR2/CR3/NEF/ARW/DNG/...) are handled by extracting the camera's
embedded preview with ExifTool and downscaling it with Pillow.  If a RAW has no
usable embedded preview the thumbnail is skipped gracefully — the original file
is *never* modified.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import uuid
from pathlib import Path
from typing import Optional

from PIL import Image, ImageOps

from . import exiftool, paths
from .models import RASTER_EXTENSIONS

log = logging.getLogger(__name__)

#: Bump when the thumbnail rendering changes so old cache entries are ignored.
CACHE_VERSION = "3"

#: Aspect ratio of every generated thumbnail (width / height).
THUMB_ASPECT = 4 / 3
#: Height of a generated thumbnail in pixels; the width follows from the ratio.
THUMB_HEIGHT = 300
THUMB_WIDTH = round(THUMB_HEIGHT * THUMB_ASPECT)  # 400
#: Longest edge used when decoding the source image.
THUMB_MAX_EDGE = max(THUMB_WIDTH, THUMB_HEIGHT)
JPEG_QUALITY = 85


# ---------------------------------------------------------------------------
# Cache location
# ---------------------------------------------------------------------------


def cache_name(path: str | Path, size: Optional[int], mtime: Optional[float]) -> str:
    """Deterministic cache file name (without extension) for a photo.

    Includes :data:`CACHE_VERSION` so a change in the rendering (e.g. the move
    to 4:3 canvases) invalidates every previously cached thumbnail.
    """
    key = f"{CACHE_VERSION}|{Path(path).resolve()}|{size}|{mtime}"
    return hashlib.sha1(key.encode("utf-8", "replace")).hexdigest()


def thumbnail_path(
    path: str | Path, size: Optional[int], mtime: Optional[float]
) -> Path:
    """Return where the thumbnail for *path* is (or would be) stored."""
    return paths.thumbnail_cache_dir() / f"{cache_name(path, size, mtime)}.jpg"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_thumbnail(
    path: str | Path,
    size: Optional[int] = None,
    mtime: Optional[float] = None,
    force: bool = False,
) -> Optional[Path]:
    """Return the path to a thumbnail JPEG for *path*, generating if needed.

    Returns ``None`` when no thumbnail could be produced.  Existing cache files
    are reused unless *force* is true.  Failures never raise.
    """
    src = Path(path)
    out = thumbnail_path(src, size, mtime)

    if out.is_file() and not force:
        return out

    if not src.is_file():
        # Nothing to render; keep a previously cached copy if it exists.
        return out if out.is_file() else None

    ext = src.suffix.lower().lstrip(".")
    try:
        if ext in RASTER_EXTENSIONS:
            image = _load_raster(src)
        else:
            image = _load_raw_preview(src)

        if image is None:
            return out if out.is_file() else None

        _write_thumbnail(image, out)
        return out if out.is_file() else None
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("Thumbnail generation failed for %s: %s", src, exc)
        return out if out.is_file() else None


def preview_bytes(path: str | Path) -> Optional[bytes]:
    """Return displayable image bytes for *path*.

    Used by the full-screen viewer for RAW files, which Qt cannot decode itself.
    Raster files return ``None`` so the caller loads the original directly.
    """
    src = Path(path)
    ext = src.suffix.lower().lstrip(".")
    if ext in RASTER_EXTENSIONS:
        return None
    return exiftool.read_binary_preview(src)


def clear_cache() -> int:
    """Delete all cached thumbnails. Returns the number removed."""
    removed = 0
    for item in paths.thumbnail_cache_dir().glob("*.jpg"):
        try:
            item.unlink()
            removed += 1
        except OSError:
            pass
    return removed


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _load_raster(src: Path):
    """Open a raster image and return a fully decoded, detached copy."""
    with Image.open(src) as img:
        # ``draft`` lets libjpeg decode at a reduced scale — much faster on
        # multi-megapixel JPEGs and lossless for the purpose of thumbnails.
        try:
            img.draft("RGB", (THUMB_MAX_EDGE, THUMB_MAX_EDGE))
        except Exception:
            pass
        img = ImageOps.exif_transpose(img)
        # ``convert`` returns a new image, so it stays usable after the file
        # handle is closed.
        return img.convert("RGB")


def _load_raw_preview(src: Path):
    """Extract and decode the embedded preview of a RAW file."""
    data = exiftool.read_binary_preview(src)
    if not data:
        log.debug("No embedded preview available for %s", src)
        return None

    try:
        with Image.open(io.BytesIO(data)) as img:
            img = ImageOps.exif_transpose(img)
            return img.convert("RGB")
    except Exception as exc:
        log.debug("Embedded preview for %s could not be decoded: %s", src, exc)
        return None


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def _write_thumbnail(image, out: Path) -> None:
    """Crop *image* to 4:3 (centre) and write it atomically to *out*.

    The image is scaled so it *covers* the 4:3 canvas and the overflowing edges
    are trimmed — nothing is squashed and no letterbox bars are added.  The crop
    is centred, keeping the middle of the frame.
    """
    if image.mode != "RGB":
        image = image.convert("RGB")

    # ``ImageOps.fit`` scales to cover the target box and centre-crops the rest.
    canvas = ImageOps.fit(
        image, (THUMB_WIDTH, THUMB_HEIGHT), method=Image.Resampling.LANCZOS
    )

    # Write to a unique temp file in the same directory, then replace.  This
    # prevents a concurrently-reading UI thread from seeing a partial JPEG.
    tmp = out.with_name(f".{out.stem}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        canvas.save(tmp, "JPEG", quality=JPEG_QUALITY, optimize=True)
        os.replace(tmp, out)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _no_window() -> int:  # retained for backwards compatibility
    return exiftool.no_window_kwargs().get("creationflags", 0)