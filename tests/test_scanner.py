"""Tests for the recursive scanner."""

from __future__ import annotations

from core.catalog import Catalog, PhotoQuery
from core.scanner import Scanner
from tests.conftest import make_jpeg


def _build_tree(root):
    make_jpeg(root / "2020" / "a.jpg", exif_datetime="2020:05:01 12:00:00")
    make_jpeg(root / "2020" / "nested" / "b.jpg", exif_datetime="2020:05:02 09:30:00")
    make_jpeg(root / "2021" / "c.png".replace("png", "jpg"))
    # Ignored files:
    (root / "notes.txt").write_text("ignore me")
    (root / ".hidden").mkdir()
    make_jpeg(root / ".hidden" / "secret.jpg")


def test_scan_finds_supported_only(catalog: Catalog, tmp_path):
    lib = catalog.add_library(tmp_path)
    _build_tree(tmp_path)
    result = Scanner(catalog).scan_library(lib)
    assert result.found == 3
    assert result.added == 3
    assert catalog.count_photos(lib.id) == 3


def test_rescan_no_duplicates(catalog: Catalog, tmp_path):
    lib = catalog.add_library(tmp_path)
    make_jpeg(tmp_path / "a.jpg")
    Scanner(catalog).scan_library(lib)
    first = catalog.count_photos(lib.id)
    result = Scanner(catalog).scan_library(lib)
    assert first == 1
    assert catalog.count_photos(lib.id) == 1
    assert result.added == 0


def test_scan_detects_missing(catalog: Catalog, tmp_path):
    lib = catalog.add_library(tmp_path)
    a = make_jpeg(tmp_path / "a.jpg")
    make_jpeg(tmp_path / "b.jpg")
    Scanner(catalog).scan_library(lib)
    a.unlink()
    result = Scanner(catalog).scan_library(lib)
    assert result.missing == 1
    missing = catalog.search(PhotoQuery(missing=True))
    assert [p.file_name for p in missing] == ["a.jpg"]


def test_scan_reads_date(catalog: Catalog, tmp_path):
    lib = catalog.add_library(tmp_path)
    make_jpeg(tmp_path / "dated.jpg", exif_datetime="2019:03:04 15:16:17")
    Scanner(catalog).scan_library(lib)
    photo = catalog.search(PhotoQuery())[0]
    assert photo.date_taken is not None
    assert photo.date_taken.startswith("2019-03-04T15:16:17")


def test_scan_handles_unavailable_library(catalog: Catalog, tmp_path):
    lib = catalog.add_library(tmp_path / "does_not_exist")
    result = Scanner(catalog).scan_library(lib)
    assert result.errors >= 1
    assert catalog.get_library(lib.id).is_available is False


def test_rescan_skips_unchanged_files(catalog: Catalog, tmp_path):
    lib = catalog.add_library(tmp_path)
    make_jpeg(tmp_path / "a.jpg")
    make_jpeg(tmp_path / "b.jpg")
    Scanner(catalog).scan_library(lib)

    result = Scanner(catalog).scan_library(lib)
    assert result.skipped == 2
    assert result.added == 0
    assert result.updated == 0


def test_rescan_does_not_reextract_metadata(catalog: Catalog, tmp_path, monkeypatch):
    """Unchanged files must not pay the (expensive) metadata cost again."""
    lib = catalog.add_library(tmp_path)
    make_jpeg(tmp_path / "a.jpg")
    Scanner(catalog).scan_library(lib)

    calls: list = []

    def fail_extract(path):
        calls.append(path)
        raise AssertionError("metadata should not be re-read")

    monkeypatch.setattr("core.scanner.metadata.extract", fail_extract)
    result = Scanner(catalog).scan_library(lib)
    assert result.skipped == 1
    assert calls == []


def test_rescan_updates_changed_file(catalog: Catalog, tmp_path):
    lib = catalog.add_library(tmp_path)
    path = tmp_path / "changing.jpg"
    make_jpeg(path, size=(800, 600))
    Scanner(catalog).scan_library(lib)

    make_jpeg(path, size=(200, 150), color=(1, 2, 3))
    result = Scanner(catalog).scan_library(lib)
    assert result.updated == 1
    assert catalog.count_photos(lib.id) == 1


def test_cancelled_scan_does_not_flag_missing(catalog: Catalog, tmp_path):
    lib = catalog.add_library(tmp_path)
    make_jpeg(tmp_path / "a.jpg")
    make_jpeg(tmp_path / "b.jpg")
    Scanner(catalog).scan_library(lib)

    result = Scanner(catalog, cancel_check=lambda: True).scan_library(lib)
    assert result.cancelled is True
    assert result.missing == 0
    assert catalog.count_photos(lib.id) == 2


def test_scan_ignores_system_directories(catalog: Catalog, tmp_path):
    lib = catalog.add_library(tmp_path)
    make_jpeg(tmp_path / "good.jpg")
    make_jpeg(tmp_path / "@eaDir" / "thumb.jpg")
    result = Scanner(catalog).scan_library(lib)
    assert result.found == 1