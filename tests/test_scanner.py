"""Tests for the recursive scanner."""

from __future__ import annotations

from pathlib import Path

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


def test_scan_imports_exif_location_and_keeps_manual_edits(
    catalog: Catalog, tmp_path, monkeypatch
):
    from core.metadata import PhotoMetadata

    lib = catalog.add_library(tmp_path)
    path = make_jpeg(tmp_path / "geo.jpg")
    monkeypatch.setattr(
        "core.scanner.metadata.extract",
        lambda _path: PhotoMetadata(width=800, height=600, latitude=10.0,
                                    longitude=20.0),
    )
    Scanner(catalog).scan_library(lib)

    photo = catalog.search(PhotoQuery())[0]
    assert (photo.latitude, photo.longitude) == (10.0, 20.0)
    assert photo.geo_source == "exif"

    # A manual correction must survive a later (changed-file) rescan.
    catalog.set_photo_location(photo.id, 55.7558, 37.6173)
    make_jpeg(path, size=(640, 480), color=(1, 2, 3))
    Scanner(catalog).scan_library(lib)

    updated = catalog.get_photo(photo.id)
    assert (updated.latitude, updated.longitude) == (55.7558, 37.6173)
    assert updated.geo_source == "manual"


def test_pending_geo_rows_are_read_once(catalog: Catalog, tmp_path, monkeypatch):
    """Photos indexed before geotags existed get their EXIF location on rescan."""
    from core.metadata import PhotoMetadata

    lib = catalog.add_library(tmp_path)
    make_jpeg(tmp_path / "old.jpg")
    Scanner(catalog).scan_library(lib)
    pid = catalog.search(PhotoQuery())[0].id
    catalog.db.execute(
        "UPDATE photos SET geo_source = 'pending', latitude = NULL, longitude = NULL "
        "WHERE id = ?",
        (pid,),
    )
    catalog.commit()

    monkeypatch.setattr(
        "core.scanner.metadata.extract",
        lambda _path: PhotoMetadata(width=800, height=600, latitude=10.0,
                                    longitude=20.0),
    )
    Scanner(catalog).scan_library(lib)

    photo = catalog.get_photo(pid)
    assert (photo.latitude, photo.longitude) == (10.0, 20.0)
    assert photo.geo_source == "exif"


def test_failed_metadata_read_keeps_known_location(catalog: Catalog, tmp_path, monkeypatch):
    from core.metadata import PhotoMetadata

    lib = catalog.add_library(tmp_path)
    path = make_jpeg(tmp_path / "kept.jpg")
    monkeypatch.setattr(
        "core.scanner.metadata.extract",
        lambda _path: PhotoMetadata(width=800, height=600, latitude=10.0,
                                    longitude=20.0),
    )
    Scanner(catalog).scan_library(lib)

    # The file changes, but this time the metadata read comes back empty.
    monkeypatch.setattr("core.scanner.metadata.extract", lambda _path: PhotoMetadata())
    make_jpeg(path, size=(640, 480), color=(1, 2, 3))
    Scanner(catalog).scan_library(lib)

    photo = catalog.search(PhotoQuery())[0]
    assert (photo.latitude, photo.longitude) == (10.0, 20.0)


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


def test_moved_file_keeps_tags_within_one_scan(catalog: Catalog, tmp_path):
    """Moving a photo to another subfolder must not lose its tags.

    The move happens between two scans; the reconnect has to succeed in that
    second scan *before* ``mark_missing`` flags the old row, otherwise a
    duplicate without tags is created and the original is orphaned.
    """
    lib = catalog.add_library(tmp_path)
    src = tmp_path / "krasnodar" / "204.tif"
    make_jpeg(src, color=(10, 20, 30))
    Scanner(catalog).scan_library(lib)

    photo = catalog.search(PhotoQuery())[0]
    category = catalog.list_categories()[0]
    tag = catalog.create_tag("204", category.id)
    catalog.add_tag_to_photo(photo.id, tag.id)

    # Move the file to a sibling folder and rescan.
    dst = tmp_path / "evpatoria" / "204.tif"
    dst.parent.mkdir(parents=True)
    src.rename(dst)

    result = Scanner(catalog).scan_library(lib)

    assert result.relocated == 1
    assert result.added == 0
    assert catalog.count_photos(lib.id) == 1
    assert result.missing == 0
    only = catalog.search(PhotoQuery())[0]
    assert Path(only.path) == dst.resolve()
    assert [t.name for t in catalog.get_photo(only.id).tags] == ["204"]