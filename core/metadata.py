"""High-level metadata extraction.

Produces a normalised :class:`PhotoMetadata` for a file, preferring ExifTool
(complete, RAW-aware) and falling back to Pillow for the common raster formats
when ExifTool is unavailable.

The shooting date is taken from EXIF ``DateTimeOriginal`` first, exactly as the
specification requires.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from . import exiftool
from .geo import is_valid, parse_gps_value
from .models import RASTER_EXTENSIONS, normalise_exif_datetime


@dataclass
class PhotoMetadata:
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
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    def as_db_tuple(self) -> dict:
        return asdict(self)

    def non_empty(self) -> dict:
        """Return only the fields that actually carry a value."""
        return {
            key: value
            for key, value in asdict(self).items()
            if value not in (None, "")
        }


def extract(path: str | Path) -> PhotoMetadata:
    """Extract normalised metadata for *path* (never modifies the file)."""
    path = Path(path)
    ext = path.suffix.lower().lstrip(".")
    meta = PhotoMetadata()

    used_exiftool = False
    if exiftool.is_available():
        data = exiftool.extract(path)
        if data:
            _apply_exiftool(meta, data)
            used_exiftool = True

    # Gap-fill (and the only source when ExifTool is absent) for raster files.
    if ext in RASTER_EXTENSIONS and (
        not used_exiftool or meta.width is None or meta.date_taken is None
    ):
        _apply_pillow(meta, path)

    return meta


# ---------------------------------------------------------------------------
# ExifTool
# ---------------------------------------------------------------------------


def _apply_exiftool(meta: PhotoMetadata, data: dict) -> None:
    meta.width = _as_int(_first(data, "width", "ImageWidth", "ExifImageWidth"))
    meta.height = _as_int(_first(data, "height", "ImageHeight", "ExifImageHeight"))
    meta.date_taken = normalise_exif_datetime(
        _clean(_first(data, "date_taken", "DateTimeOriginal", "CreateDate"))
    )
    meta.camera_make = _clean(_first(data, "camera_make", "Make"))
    meta.camera_model = _clean(_first(data, "camera_model", "Model", "CameraModelName"))
    meta.lens = _clean(_first(data, "lens", "LensModel", "LensID"))
    meta.iso = _clean(_first(data, "iso", "ISO"))
    meta.aperture = _pretty_aperture(_clean(_first(data, "aperture", "FNumber")))
    meta.shutter = _clean(_first(data, "shutter", "ExposureTime"))
    meta.focal_length = _clean(_first(data, "focal_length", "FocalLength"))
    meta.instance_id = _clean(
        _first(data, "instance_id", "ImageUniqueID", "InstanceID")
    )
    latitude = parse_gps_value(
        _first(data, "latitude", "GPSLatitude"),
        _first(data, "latitude_ref", "GPSLatitudeRef"),
    )
    longitude = parse_gps_value(
        _first(data, "longitude", "GPSLongitude"),
        _first(data, "longitude_ref", "GPSLongitudeRef"),
    )
    if is_valid(latitude, longitude):
        meta.latitude = latitude
        meta.longitude = longitude


# ---------------------------------------------------------------------------
# Pillow
# ---------------------------------------------------------------------------


def _apply_pillow(meta: PhotoMetadata, path: Path) -> None:
    try:
        from PIL import Image, ExifTags
    except ImportError:  # pragma: no cover - Pillow is a hard requirement
        return

    try:
        with Image.open(path) as img:
            if meta.width is None:
                meta.width = img.width
            if meta.height is None:
                meta.height = img.height

            exif = img.getexif()
            if not exif:
                return

            # Build a tag-name -> value map for IFD0 + the Exif sub-IFD.  GPS uses its
            # own tag namespace (GPSTAGS), otherwise tags 2/4 collide with the
            # names of unrelated Exif tags.
            tags: dict = {ExifTags.TAGS.get(k, k): v for k, v in exif.items()}
            if hasattr(exif, "get_ifd"):
                for ifd_id in (0x8769, 0x8825):  # Exif, GPSInfo
                    sub = exif.get_ifd(ifd_id)
                    if sub:
                        names = (
                            ExifTags.GPSTAGS
                            if ifd_id == 0x8825
                            else ExifTags.TAGS
                        )
                        tags.update({names.get(k, k): v for k, v in sub.items()})

            if meta.date_taken is None:
                meta.date_taken = normalise_exif_datetime(
                    _clean(tags.get("DateTimeOriginal"))
                )
            if meta.camera_make is None:
                meta.camera_make = _clean(tags.get("Make"))
            if meta.camera_model is None:
                meta.camera_model = _clean(tags.get("Model"))
            if meta.iso is None:
                meta.iso = _clean(
                    tags.get("ISOSpeedRatings") or tags.get("PhotographicSensitivity")
                )
            if meta.aperture is None:
                meta.aperture = _format_aperture(tags.get("FNumber"))
            if meta.shutter is None:
                meta.shutter = _format_shutter(tags.get("ExposureTime"))
            if meta.focal_length is None:
                meta.focal_length = _format_focal(tags.get("FocalLength"))
            if meta.lens is None:
                meta.lens = _clean(tags.get("LensModel"))
            if meta.latitude is None or meta.longitude is None:
                latitude = parse_gps_value(
                    tags.get("GPSLatitude"), tags.get("GPSLatitudeRef")
                )
                longitude = parse_gps_value(
                    tags.get("GPSLongitude"), tags.get("GPSLongitudeRef")
                )
                if is_valid(latitude, longitude):
                    meta.latitude = latitude
                    meta.longitude = longitude
    except Exception:
        # Corrupt / unsupported file — keep whatever we already have.
        return


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clean(value) -> Optional[str]:
    """Normalise an EXIF value to a display string.

    Handles ``bytes`` (NUL padded), tuples (takes the first entry) and Pillow's
    rational types, which must not be rendered as ``"(1, 200)"``.
    """
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.split(b"\x00", 1)[0].decode("utf-8", "replace")
    elif isinstance(value, (tuple, list)):
        if not value:
            return None
        value = value[0]
    text = str(value).strip().replace("\x00", "")
    return text or None


def _first(data: dict, *keys):
    for key in keys:
        value = data.get(key)
        if value not in (None, "", "-"):
            return value
    return None


def _as_int(value) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _format_shutter(value) -> Optional[str]:
    """Render an exposure time as ``1/250`` style text."""
    if value is None:
        return None
    try:
        ratio = float(value)
    except (TypeError, ValueError):
        return _clean(value)
    if ratio <= 0:
        return None
    if ratio < 1:
        return f"1/{round(1 / ratio)}"
    return f"{ratio:g}s"


def _format_aperture(value) -> Optional[str]:
    if value is None:
        return None
    try:
        return f"f/{float(value):g}"
    except (TypeError, ValueError):
        return _clean(value)


def _pretty_aperture(value: Optional[str]) -> Optional[str]:
    """ExifTool reports ``FNumber`` as ``2.8``; present it as ``f/2.8``."""
    if not value:
        return value
    if value.startswith("f/"):
        return value
    try:
        float(value)
    except ValueError:
        return value
    return f"f/{value}"


def _format_focal(value) -> Optional[str]:
    if value is None:
        return None
    try:
        return f"{float(value):g} мм"
    except (TypeError, ValueError):
        return _clean(value)