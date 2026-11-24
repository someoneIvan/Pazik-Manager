"""Tests for the RAW pipeline: metadata and embedded previews via ExifTool.

ExifTool is faked here so the tests run on machines without it installed;
the wrapper, metadata normalisation and thumbnail extraction logic are real.
"""

from __future__ import annotations

from PIL import Image

from core import exiftool, metadata, thumbnail
from tests.conftest import make_fake_raw, make_jpeg, make_preview_jpeg


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


def test_exiftool_extract_maps_friendly_fields(fake_exiftool, tmp_path):
    raw = make_fake_raw(tmp_path / "IMG_0001.CR2")
    data = exiftool.extract(raw)
    assert data["width"] == "6720"
    assert data["camera_make"] == "Canon"
    assert data["camera_model"] == "Canon EOS 5D Mark IV"
    assert data["instance_id"] == "ABC123DEF456"
    # Human-readable formatting is preserved (no -n flag).
    assert data["shutter"] == "1/250"


def test_metadata_raw_dimensions_and_date(fake_exiftool, tmp_path):
    raw = make_fake_raw(tmp_path / "IMG_0001.CR2")
    meta = metadata.extract(raw)
    assert meta.width == 6720
    assert meta.height == 4480
    assert meta.date_taken == "2021-06-07T08:09:10"
    assert meta.camera_make == "Canon"
    assert meta.aperture == "f/4.0"
    assert meta.shutter == "1/250"
    assert meta.iso == "200"
    assert meta.focal_length is None  # not present in the fake payload


def test_metadata_date_prefers_datetime_original(fake_exiftool, tmp_path):
    fake_exiftool.tags = {
        "DateTimeOriginal": "2019:01:02 03:04:05",
        "CreateDate": "2020:09:09 09:09:09",
        "ImageWidth": "100",
        "ImageHeight": "50",
    }
    raw = make_fake_raw(tmp_path / "x.NEF")
    meta = metadata.extract(raw)
    assert meta.date_taken.startswith("2019-01-02T03:04:05")


def test_metadata_falls_back_to_combined_image_size(fake_exiftool, tmp_path):
    fake_exiftool.tags = {
        "DateTimeOriginal": "2021:01:02 03:04:05",
        "ImageSize": "6000x4000",
    }
    raw = make_fake_raw(tmp_path / "size.CR3")
    meta = metadata.extract(raw)
    assert meta.width == 6000
    assert meta.height == 4000


def test_metadata_without_exiftool_falls_back_to_pillow(monkeypatch, tmp_path):
    from core import exiftool as et

    monkeypatch.setattr(et, "is_available", lambda: False)
    img = make_jpeg(tmp_path / "plain.jpg", size=(320, 240),
                    exif_datetime="2018:11:12 13:14:15")
    meta = metadata.extract(img)
    assert meta.width == 320
    assert meta.height == 240
    assert meta.date_taken.startswith("2018-11-12T13:14:15")


# ---------------------------------------------------------------------------
# Embedded previews / thumbnails
# ---------------------------------------------------------------------------


def test_raw_thumbnail_generated_and_downscaled(fake_exiftool, tmp_path):
    raw = make_fake_raw(tmp_path / "IMG_0001.CR2")
    size = raw.stat().st_size
    mtime = raw.stat().st_mtime

    out = thumbnail.get_thumbnail(raw, size, mtime)
    assert out is not None and out.is_file()
    with Image.open(out) as img:
        assert max(img.size) <= thumbnail.THUMB_MAX_EDGE
        assert img.format == "JPEG"


def test_raw_thumbnail_is_cached(fake_exiftool, tmp_path):
    raw = make_fake_raw(tmp_path / "cached.CR2")
    size, mtime = raw.stat().st_size, raw.stat().st_mtime
    first = thumbnail.get_thumbnail(raw, size, mtime)
    calls_before = len(fake_exiftool.calls)
    second = thumbnail.get_thumbnail(raw, size, mtime)
    assert first == second
    assert len(fake_exiftool.calls) == calls_before  # no new ExifTool call


def test_preview_falls_back_when_first_tag_is_empty(fake_exiftool, tmp_path):
    fake_exiftool.previews = {"JpgFromRaw": make_preview_jpeg()}
    raw = make_fake_raw(tmp_path / "fallback.CR3")
    data = exiftool.read_binary_preview(raw)
    assert data is not None and data.startswith(b"\xff\xd8\xff")


def test_preview_rejects_non_image_bytes(fake_exiftool, tmp_path):
    fake_exiftool.previews = {
        "PreviewImage": b"<not an image>",
        "JpgFromRaw": make_preview_jpeg(),
    }
    raw = make_fake_raw(tmp_path / "odd.CR2")
    data = exiftool.read_binary_preview(raw)
    assert data is not None and data.startswith(b"\xff\xd8\xff")


def test_preview_returns_none_without_exiftool(monkeypatch, tmp_path):
    from core import exiftool as et

    monkeypatch.setattr(et, "get_exiftool_path", lambda: None)
    raw = make_fake_raw(tmp_path / "no_tool.ARW")
    assert et.read_binary_preview(raw) is None
    assert thumbnail.get_thumbnail(raw) is None


def test_preview_bytes_only_for_raw(fake_exiftool, tmp_path):
    img = make_jpeg(tmp_path / "raster.jpg")
    assert thumbnail.preview_bytes(img) is None

    raw = make_fake_raw(tmp_path / "preview.CR2")
    data = thumbnail.preview_bytes(raw)
    assert data is not None and data.startswith(b"\xff\xd8\xff")


def test_raw_without_preview_is_handled_gracefully(fake_exiftool, tmp_path):
    fake_exiftool.previews = {}
    raw = make_fake_raw(tmp_path / "empty.RW2")
    assert thumbnail.get_thumbnail(raw) is None
    # The file must remain untouched.
    assert raw.read_bytes() == b"II*\x00" + b"\x00" * 512