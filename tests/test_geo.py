"""Tests for coordinate parsing/formatting and the geotag storage API."""

from __future__ import annotations

import math

import pytest

from core.catalog import Catalog
from core.geo import (
    format_decimal,
    format_dms,
    is_valid,
    parse_coordinates,
    parse_gps_value,
)
from tests.conftest import make_jpeg


def test_parse_plain_coordinates():
    assert parse_coordinates("55.7558, 37.6173") == (55.7558, 37.6173)
    assert parse_coordinates("55,7558; 37,6173") == (55.7558, 37.6173)
    assert parse_coordinates("55.7558 37.6173") == (55.7558, 37.6173)


def test_parse_google_and_osm_links():
    assert parse_coordinates(
        "https://www.google.com/maps/@55.7558,37.6173,15z"
    ) == (55.7558, 37.6173)
    assert parse_coordinates(
        "https://www.google.com/maps?q=55.7558,37.6173"
    ) == (55.7558, 37.6173)
    assert parse_coordinates(
        "https://www.openstreetmap.org/#map=16/55.7558/37.6173"
    ) == (55.7558, 37.6173)
    assert parse_coordinates(
        "https://www.openstreetmap.org/?mlat=55.7558&mlon=37.6173"
    ) == (55.7558, 37.6173)


def test_parse_yandex_link_swaps_lon_lat():
    assert parse_coordinates(
        "https://yandex.ru/maps/?ll=37.6173%2C55.7558&z=16"
    ) == (55.7558, 37.6173)


def test_parse_reversed_pair_and_invalid_values():
    # A valid pair entered as (lon, lat) is normalised.
    assert parse_coordinates("151.2093, -33.8688") == (-33.8688, 151.2093)
    assert parse_coordinates("not coordinates") is None
    assert parse_coordinates("95.0, 200.0") is None
    assert parse_coordinates("") is None


def test_url_numbers_are_not_taken_for_coordinates():
    # Zoom and height are query parameters, not a location: a link without
    # coordinates must be rejected instead of becoming (12, 2).
    assert parse_coordinates("https://maps.google.com/?q=Moscow&z=12&h=2") is None
    assert parse_coordinates("maps.google.com/?q=Moscow&z=12&h=2") is None
    assert parse_coordinates("https://example.com/photo/12/34") is None


def test_parse_gps_value_from_pillow_tuple_and_exiftool_text():
    # Pillow/DMS tuple: 55°45′20.88″ N
    lat = parse_gps_value(((55, 1), (45, 1), (2088, 100)), "North")
    assert math.isclose(lat, 55.7558, abs_tol=1e-6)

    # ExifTool with -c %.8f: decimal degrees plus hemisphere.
    assert parse_gps_value("37.61730000 E", "East") == 37.6173
    assert parse_gps_value("33.86880000 S", "S") == -33.8688


def test_formatting():
    assert format_decimal(55.7558, 37.6173) == "55.755800, 37.617300"
    assert format_dms(55.7558, 37.6173).startswith("55°45′")
    assert is_valid(55.7, 37.6)
    assert not is_valid(95.0, 37.6)


def test_dms_rounding_never_prints_sixty():
    # 55.9999999° rounds up to the next whole degree, not to "59′60.0″".
    assert format_dms(55.9999999, 37.0) == "56°00′00.0″ N, 37°00′00.0″ E"


def test_catalog_set_and_clear_location(catalog: Catalog, tmp_path):
    lib = catalog.add_library(tmp_path)
    pid = catalog.upsert_photo(lib.id, str(make_jpeg(tmp_path / "geo.jpg")))

    catalog.set_photo_location(pid, 55.7558, 37.6173)
    photo = catalog.get_photo(pid)
    assert photo.latitude == 55.7558
    assert photo.longitude == 37.6173
    assert photo.geo_source == "manual"

    catalog.clear_photo_location(pid)
    photo = catalog.get_photo(pid)
    assert photo.latitude is None
    assert photo.longitude is None
    # Manual clearing must survive a rescan, so the lock is kept.
    assert photo.geo_source == "manual"


def test_catalog_rejects_invalid_coordinates(catalog: Catalog, tmp_path):
    lib = catalog.add_library(tmp_path)
    pid = catalog.upsert_photo(lib.id, str(make_jpeg(tmp_path / "bad.jpg")))
    with pytest.raises(ValueError):
        catalog.set_photo_location(pid, 100.0, 37.0)
    assert catalog.get_photo(pid).latitude is None
