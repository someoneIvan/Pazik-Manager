"""Thin wrapper around the ExifTool command-line tool.

ExifTool is the most reliable way to read metadata — especially from RAW files —
but it is an external dependency.  This module:

* locates an ``exiftool`` executable (bundled copy → ``PATH`` → common install
  locations),
* exposes :func:`get_exiftool_path` and :func:`is_available`,
* runs read-only extraction returning a ``dict`` of raw tag values, and
* extracts binary embedded previews from RAW files.

Nothing here writes to the original files; only read-only operations are used.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from . import paths

log = logging.getLogger(__name__)

# Common install locations checked when exiftool is not on PATH.  macOS/Linux
# users typically have it on PATH via a package manager.
_COMMON_LOCATIONS = [
    # Windows — winget (OliverBetz.ExifTool) and the official installer
    r"C:\Program Files\exiftool\exiftool.exe",
    r"C:\Program Files (x86)\exiftool\exiftool.exe",
    r"C:\Windows\exiftool.exe",
    r"C:\exiftool\exiftool.exe",
    # Per-user installs (winget, portable copies)
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\ExifTool\ExifTool.exe"),
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links\exiftool.exe"),
    # macOS / Linux
    "/usr/local/bin/exiftool",
    "/usr/bin/exiftool",
    "/opt/homebrew/bin/exiftool",
    "/snap/bin/exiftool",
]

_cached_path: Optional[str] = None
_cached_resolved = False

#: Seconds allowed for a metadata read / preview extraction.
READ_TIMEOUT = 60
PREVIEW_TIMEOUT = 120

#: Image magic numbers used to validate a binary preview before handing it to
#: Pillow (some ExifTool tags return textual placeholders for odd files).
_IMAGE_MAGIC = (
    b"\xff\xd8\xff",       # JPEG
    b"\x89PNG\r\n\x1a\n",  # PNG
    b"II*\x00",            # TIFF (little endian)
    b"MM\x00*",            # TIFF (big endian)
    b"RIFF",               # WebP
    b"BM",                 # BMP
)


def get_exiftool_path() -> Optional[str]:
    """Return the path to a usable ``exiftool`` executable, or ``None``."""
    global _cached_path, _cached_resolved
    if _cached_resolved:
        return _cached_path

    bundled = paths.bundled_exiftool()
    if bundled:
        _cached_path = str(bundled)
        _cached_resolved = True
        return _cached_path

    found = _which("exiftool")
    if not found:
        for candidate in _COMMON_LOCATIONS:
            if Path(candidate).is_file():
                found = candidate
                break

    _cached_path = found
    _cached_resolved = True
    return _cached_path


def _which(name: str) -> Optional[str]:
    """Locate *name* on PATH, also checking the Windows PATHEXT extensions."""
    found = shutil.which(name)
    if found:
        return found
    if os.name == "nt":
        # shutil.which already honours PATHEXT, but be defensive: some installs
        # ship "exiftool.exe" while PATH contains a directory we can scan.
        for ext in os.environ.get("PATHEXT", ".EXE;.CMD;.BAT").split(os.pathsep):
            found = shutil.which(name + ext.lower())
            if found:
                return found
    return None


def is_available() -> bool:
    """Return ``True`` if a working ExifTool executable was found."""
    return get_exiftool_path() is not None


def reset_cache() -> None:
    """Forget the resolved executable path (e.g. after installing ExifTool)."""
    global _cached_path, _cached_resolved
    _cached_path = None
    _cached_resolved = False


def no_window_kwargs() -> dict:
    """Extra ``subprocess`` kwargs that suppress console windows on Windows."""
    if sys.platform == "win32":
        return {"creationflags": 0x08000000}  # CREATE_NO_WINDOW
    return {}


# Tags we care about, mapped to the friendly field names used by the catalogue.
# Using ExifTool tag names keeps extraction independent of the EXIF IFD layout.
# The first available tag in each list wins.
_TAG_MAP: dict[str, tuple[str, ...]] = {
    "date_taken": ("DateTimeOriginal", "CreateDate", "DateTimeDigitized", "ModifyDate"),
    "camera_make": ("Make",),
    "camera_model": ("Model", "CameraModelName"),
    "lens": ("LensModel", "LensID", "Lens"),
    "iso": ("ISO",),
    "aperture": ("FNumber", "ApertureValue"),
    "shutter": ("ExposureTime", "ShutterSpeedValue"),
    "focal_length": ("FocalLength", "FocalLengthIn35mmFormat"),
    "instance_id": ("ImageUniqueID", "InstanceID", "OriginalDocumentID"),
    "width": ("ImageWidth", "ExifImageWidth"),
    "height": ("ImageHeight", "ExifImageHeight"),
}


def extract(path: str | Path) -> dict:
    """Read metadata for *path* via ExifTool.

    Returns a dict with friendly keys (see :data:`_TAG_MAP`) plus the raw
    ``exiftool`` output under ``"raw"`` when available.  Returns an empty dict
    on any failure so callers can fall back to Pillow.
    """
    exe = get_exiftool_path()
    if not exe:
        return {}

    # ``-m`` tolerates minor file errors (common with slightly damaged RAWs);
    # values stay human readable (e.g. "1/250") which is what the UI shows.
    cmd = [exe, "-json", "-m"]
    # Filename charset: on POSIX the filesystem encoding is UTF-8, so tell
    # ExifTool to read filenames as UTF-8.  On Windows the standalone
    # ``exiftool.exe`` receives argv through the system ANSI code page (e.g.
    # CP1251), so forcing UTF-8 makes it reject non-ASCII paths with
    # "Invalid filename encoding" — omit it there and let ExifTool use the
    # system code page, which matches what it actually receives.
    if sys.platform != "win32":
        cmd += ["-charset", "filename=utf8"]
    cmd.append(str(path))
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=READ_TIMEOUT,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("ExifTool metadata read failed for %s: %s", path, exc)
        return {}

    if not result.stdout:
        log.debug(
            "ExifTool returned %s for %s: %s",
            result.returncode,
            path,
            result.stderr[:200],
        )
        return {}

    # Some files produce warning exit codes but still valid JSON; parse first.
    try:
        data = json.loads(result.stdout.decode("utf-8", "replace"))
    except (ValueError, UnicodeDecodeError):
        log.debug("ExifTool produced invalid JSON for %s", path)
        return {}
    if not isinstance(data, list) or not data:
        return {}

    raw = data[0]
    out: dict = {"raw": raw}
    for field, candidates in _TAG_MAP.items():
        for tag in candidates:
            if tag in raw and raw[tag] not in (None, "", "-"):
                out[field] = raw[tag]
                break

    # Some cameras only expose a combined "ImageSize" ("6000x4000") string.
    if "width" not in out or "height" not in out:
        size_text = raw.get("ImageSize") or raw.get("RawImageFullSize")
        if isinstance(size_text, str):
            parts = size_text.lower().replace(" ", "").split("x")
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                out.setdefault("width", parts[0])
                out.setdefault("height", parts[1])
    return out


def read_binary_preview(
    path: str | Path,
    tags: tuple[str, ...] = (
        "PreviewImage",
        "JpgFromRaw",
        "OtherImage",
        "ThumbnailImage",
        "PhotoshopThumbnail",
    ),
) -> Optional[bytes]:
    """Return an embedded preview image extracted from *path*, or ``None``.

    Tries each ExifTool tag in order and validates the returned bytes look like
    an actual image before accepting them.  This is the RAW path: the original
    file is only read.
    """
    exe = get_exiftool_path()
    if not exe:
        return None

    for tag in tags:
        try:
            result = subprocess.run(
                [exe, "-b", "-m", f"-{tag}", str(path)],
                capture_output=True,
                timeout=PREVIEW_TIMEOUT,
                **no_window_kwargs(),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            log.debug("ExifTool preview (%s) failed for %s: %s", tag, path, exc)
            continue

        data = result.stdout
        if result.returncode != 0 or not data:
            continue
        if _looks_like_image(data):
            return data
        log.debug("Tag %s for %s did not yield a valid image", tag, path)
    return None


def _looks_like_image(data: bytes) -> bool:
    return any(data.startswith(magic) for magic in _IMAGE_MAGIC)