"""Geotag helpers: validation, parsing and formatting.

The application stores coordinates as signed decimal degrees (WGS-84).  This
module keeps the pure logic away from the widgets so it can be tested without a
Qt event loop.
"""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import unquote

_NUM = r"[-+]?\d+(?:[.,]\d+)?"


def is_valid(latitude: Optional[float], longitude: Optional[float]) -> bool:
    """Return ``True`` when both coordinates are within the real-world range."""
    if latitude is None or longitude is None:
        return False
    return -90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0


def _to_float(value: str) -> float:
    return float(value.replace(",", "."))


def _ordered(a: float, b: float) -> Optional[tuple[float, float]]:
    """Return (lat, lon), swapping when only the reversed order is valid."""
    if is_valid(a, b):
        return a, b
    if is_valid(b, a):
        return b, a
    return None


def _rational(value) -> float:
    if isinstance(value, (tuple, list)) and len(value) == 2:
        numerator, denominator = value
        return float(numerator) / float(denominator) if denominator else 0.0
    return float(value)


def _hemisphere(ref) -> Optional[str]:
    if ref is None:
        return None
    text = str(ref).strip().upper()
    if not text:
        return None
    for letter, names in (("N", ("N", "NORTH")), ("S", ("S", "SOUTH")),
                          ("E", ("E", "EAST")), ("W", ("W", "WEST"))):
        if text.startswith(letter) or text.startswith(names[1]):
            return letter
    return None


def parse_gps_value(value, ref=None) -> Optional[float]:
    """Convert an EXIF GPS component (DMS tuple or text) to decimal degrees.

    Handles both Pillow's ``(degrees, minutes, seconds)`` rational tuples and
    ExifTool's human-readable strings (``55 deg 45' 20.88" N`` or the decimal
    form produced by ``-c %.8f``).  ``ref`` may be ``N``/``S``/``E``/``W`` or a
    full word such as ``North``.
    """
    if value is None:
        return None

    if isinstance(value, (tuple, list)):
        try:
            parts = [_rational(part) for part in value[:3]]
        except (TypeError, ValueError, ZeroDivisionError):
            return None
        while len(parts) < 3:
            parts.append(0.0)
        result = parts[0] + parts[1] / 60.0 + parts[2] / 3600.0
        hemisphere = _hemisphere(ref)
    else:
        text = str(value).strip()
        if not text:
            return None
        numbers = re.findall(_NUM, text)
        if not numbers:
            return None
        try:
            nums = [_to_float(number) for number in numbers]
        except ValueError:
            return None
        if len(nums) >= 3:
            result = nums[0] + nums[1] / 60.0 + nums[2] / 3600.0
        else:
            result = nums[0]
        hemisphere = _hemisphere(ref)
        if hemisphere is None:
            match = re.search(r"([NSEW])\s*$", text, re.IGNORECASE)
            hemisphere = match.group(1).upper() if match else None

    if hemisphere in ("S", "W"):
        result = -abs(result)
    elif hemisphere in ("N", "E"):
        result = abs(result)
    return result


def parse_coordinates(text: str | None) -> Optional[tuple[float, float]]:
    """Extract ``(latitude, longitude)`` from text or a map link.

    Accepts plain ``55.7558, 37.6173``, ``geo:`` URIs and links from Google
    Maps, Yandex Maps and OpenStreetMap.  Returns ``None`` when no valid pair is
    found.  Decimal commas are supported.
    """
    if not text:
        return None
    value = unquote(text.strip())
    if not value:
        return None
    low = value.lower()

    # Explicit latitude/longitude query parameters.
    lat_match = re.search(r"[?&;]lat(?:itude)?=(" + _NUM + r")", value, re.I)
    lon_match = re.search(
        r"[?&;](?:lon|lng|longitude)=(" + _NUM + r")", value, re.I
    )
    if lat_match and lon_match:
        result = _ordered(_to_float(lat_match.group(1)), _to_float(lon_match.group(1)))
        if result:
            return result

    # OpenStreetMap: #map=zoom/lat/lon, or mlat=…&mlon=…
    match = re.search(
        r"#map=[\d.]+/(" + _NUM + r")/(" + _NUM + r")", value, re.I
    )
    if match:
        result = _ordered(_to_float(match.group(1)), _to_float(match.group(2)))
        if result:
            return result
    mlat = re.search(r"[?&;]mlat=(" + _NUM + r")", value, re.I)
    mlon = re.search(r"[?&;]mlon=(" + _NUM + r")", value, re.I)
    if mlat and mlon:
        result = _ordered(_to_float(mlat.group(1)), _to_float(mlon.group(1)))
        if result:
            return result

    # Google/Apple style "@lat,lon,zoom".
    match = re.search(r"@(" + _NUM + r")\s*,\s*(" + _NUM + r")", value)
    if match:
        result = _ordered(_to_float(match.group(1)), _to_float(match.group(2)))
        if result:
            return result

    # geo:lat,lon URI.
    match = re.search(r"\bgeo:(" + _NUM + r")\s*,\s*(" + _NUM + r")", value, re.I)
    if match:
        result = _ordered(_to_float(match.group(1)), _to_float(match.group(2)))
        if result:
            return result

    # Yandex uses lon,lat in ll= and pt= parameters.
    if "yandex" in low:
        for key in ("ll", "pt"):
            match = re.search(
                key + r"=(" + _NUM + r")\s*,\s*(" + _NUM + r")", value, re.I
            )
            if match:
                a, b = _to_float(match.group(1)), _to_float(match.group(2))
                result = _ordered(b, a)
                if result:
                    return result

    # Google q=/query=/ll= use lat,lon.
    for key in ("q", "query", "ll"):
        match = re.search(
            r"[?&;]" + key + r"=(" + _NUM + r")\s*,\s*(" + _NUM + r")",
            value,
            re.I,
        )
        if match:
            result = _ordered(
                _to_float(match.group(1)), _to_float(match.group(2))
            )
            if result:
                return result

    # Plain "55.7558, 37.6173" (or space separated).  Requiring exactly two
    # numbers avoids accidentally interpreting arbitrary link parameters.
    numbers = re.findall(_NUM, value)
    if len(numbers) == 2:
        try:
            return _ordered(_to_float(numbers[0]), _to_float(numbers[1]))
        except ValueError:
            return None
    return None


def format_decimal(latitude: float, longitude: float) -> str:
    """Coordinates in the familiar ``55.755800, 37.617300`` form."""
    return f"{latitude:.6f}, {longitude:.6f}"


def _dms_part(value: float, positive: str, negative: str) -> str:
    hemisphere = positive if value >= 0 else negative
    absolute = abs(value)
    degrees = int(absolute)
    minutes_float = (absolute - degrees) * 60.0
    minutes = int(minutes_float)
    seconds = (minutes_float - minutes) * 60.0
    return f"{degrees}°{minutes:02d}′{seconds:04.1f}″ {hemisphere}"


def format_dms(latitude: float, longitude: float) -> str:
    """Human-readable degrees/minutes/seconds form."""
    return f"{_dms_part(latitude, 'N', 'S')}, {_dms_part(longitude, 'E', 'W')}"


def osm_url(latitude: float, longitude: float, zoom: int = 16) -> str:
    """OpenStreetMap page with a marker at the given point."""
    return (
        f"https://www.openstreetmap.org/?mlat={latitude:.6f}"
        f"&mlon={longitude:.6f}#map={zoom}/{latitude:.6f}/{longitude:.6f}"
    )


__all__ = [
    "format_decimal",
    "format_dms",
    "is_valid",
    "osm_url",
    "parse_coordinates",
    "parse_gps_value",
]