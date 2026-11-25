"""Tests for metadata extraction and thumbnail generation."""

from __future__ import annotations

from core import metadata, thumbnail
from tests.conftest import make_jpeg


def test_metadata_reads_dimensions(catalog, tmp_path):
    img = make_jpeg(tmp_path / "a.jpg", size=(640, 480))
    meta = metadata.extract(img)
    assert meta.width == 640
    assert meta.height == 480


def test_metadata_reads_datetime_original(tmp_path):
    img = make_jpeg(tmp_path / "a.jpg", exif_datetime="2021:12:25 08:05:00")
    meta = metadata.extract(img)
    assert meta.date_taken is not None
    assert meta.date_taken.startswith("2021-12-25T08:05:00")


def test_thumbnail_created_and_cached(tmp_path):
    img = make_jpeg(tmp_path / "a.jpg", size=(1200, 900))
    size = img.stat().st_size
    mtime = img.stat().st_mtime
    t1 = thumbnail.get_thumbnail(img, size, mtime)
    assert t1 is not None and t1.is_file()
    # Second call returns the same cached file.
    t2 = thumbnail.get_thumbnail(img, size, mtime)
    assert t1 == t2

    # Every thumbnail is an exact 4:3 rectangle.
    from PIL import Image

    with Image.open(t1) as im:
        assert im.size == (thumbnail.THUMB_WIDTH, thumbnail.THUMB_HEIGHT)
        assert im.width / im.height == 4 / 3


def test_thumbnail_missing_file(tmp_path):
    assert thumbnail.get_thumbnail(tmp_path / "nope.jpg") is None


def test_thumbnail_crops_instead_of_squashing(tmp_path):
    """A non-4:3 image must be cropped, not distorted or letterboxed."""
    from PIL import Image

    # A tall image: 4:3 cropping keeps the full width and trims top/bottom.
    tall = make_jpeg(tmp_path / "tall.jpg", size=(400, 1200), color=(200, 40, 40))
    out = thumbnail.get_thumbnail(tall)
    with Image.open(out) as im:
        assert im.size == (thumbnail.THUMB_WIDTH, thumbnail.THUMB_HEIGHT)
        # JPEG is lossy, so allow a small tolerance per channel.
        assert _close(im.getpixel((im.width // 2, im.height // 2)), (200, 40, 40))

    # A wide image: 4:3 cropping keeps the full height and trims the sides.
    wide = make_jpeg(tmp_path / "wide.jpg", size=(1600, 600), color=(40, 90, 200))
    out = thumbnail.get_thumbnail(wide)
    with Image.open(out) as im:
        assert im.size == (thumbnail.THUMB_WIDTH, thumbnail.THUMB_HEIGHT)
        assert _close(im.getpixel((im.width // 2, im.height // 2)), (40, 90, 200))


def _close(pixel, expected, tolerance=6) -> bool:
    return all(abs(a - b) <= tolerance for a, b in zip(pixel[:3], expected))


def test_normalise_exif_datetime_variants():
    from core.models import normalise_exif_datetime

    assert normalise_exif_datetime("2021:06:07 08:09:10") == "2021-06-07T08:09:10"
    assert normalise_exif_datetime("2021:06:07 08:09:10.123") == "2021-06-07T08:09:10"
    assert normalise_exif_datetime("2021:06:07 08:09:10+03:00") == "2021-06-07T08:09:10"
    assert normalise_exif_datetime("2021-06-07T08:09:10Z") == "2021-06-07T08:09:10"
    assert normalise_exif_datetime("2021:06:07") == "2021-06-07T00:00:00"
    assert normalise_exif_datetime("0000:00:00 00:00:00") is None
    assert normalise_exif_datetime("") is None
    assert normalise_exif_datetime(None) is None