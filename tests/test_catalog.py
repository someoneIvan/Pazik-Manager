"""Tests for catalogue operations: libraries, photos, tags, search."""

from __future__ import annotations

from pathlib import Path

from core import identification
from core.catalog import Catalog, PhotoQuery
from tests.conftest import make_jpeg


def _add_photo(cat, library_id, path, **fields):
    return cat.upsert_photo(library_id, str(path), **fields)


def test_add_library_deduplicates(catalog: Catalog, tmp_path):
    lib = catalog.add_library(tmp_path)
    again = catalog.add_library(tmp_path)
    assert lib.id == again.id
    assert len(catalog.list_libraries()) == 1


def test_find_overlapping_library(catalog: Catalog, tmp_path):
    root = tmp_path / "root"
    (root / "child").mkdir(parents=True)
    lib = catalog.add_library(root)

    # A folder inside the library, or a parent of it, would duplicate records.
    assert catalog.find_overlapping_library(root / "child") == lib
    assert catalog.find_overlapping_library(tmp_path) == lib

    other = tmp_path.parent / "unrelated_pazik_dir"
    other.mkdir(exist_ok=True)
    assert catalog.find_overlapping_library(other) is None


def test_relocate_library_remaps_paths_and_keeps_tags(catalog: Catalog, tmp_path):
    from core.scanner import Scanner

    old_root = tmp_path / "old"
    (old_root / "2020").mkdir(parents=True)
    make_jpeg(old_root / "2020" / "a.jpg")

    lib = catalog.add_library(old_root)
    Scanner(catalog).scan_library(lib)
    photo = catalog.search(PhotoQuery())[0]
    category = catalog.list_categories()[0]
    tag = catalog.create_tag("Трамвай", category.id)
    catalog.add_tag_to_photo(photo.id, tag.id)

    # Simulate a drive-letter / folder change and repoint the library.
    new_root = tmp_path / "new"
    old_root.rename(new_root)
    remapped = catalog.relocate_library(lib.id, new_root)

    assert remapped == 1
    updated = catalog.get_photo(photo.id)
    assert Path(updated.path) == (new_root / "2020" / "a.jpg").resolve()
    assert [t.name for t in updated.tags] == ["Трамвай"]
    assert catalog.get_library(lib.id).path == str(new_root.resolve())


def test_upsert_photo_no_duplicates(catalog: Catalog, tmp_path):
    lib = catalog.add_library(tmp_path)
    img = make_jpeg(tmp_path / "x.jpg")
    pid1 = _add_photo(catalog, lib.id, img, size_bytes=100)
    pid2 = _add_photo(catalog, lib.id, img, size_bytes=200)
    assert pid1 == pid2
    assert catalog.count_photos(lib.id) == 1
    # Second upsert updated the size.
    photo = catalog.get_photo(pid1)
    assert photo.size_bytes == 200


def test_tag_crud(catalog: Catalog):
    cat = catalog.list_categories()[0]
    tag = catalog.create_tag("Bus 1234", cat.id)
    assert tag.name == "Bus 1234"
    # Creating the same tag again reuses it.
    again = catalog.create_tag("Bus 1234", cat.id)
    assert again.id == tag.id
    catalog.rename_tag(tag.id, "Bus 5678")
    assert catalog._get_tag(tag.id).name == "Bus 5678"
    catalog.delete_tag(tag.id)
    assert all(t.id != tag.id for t in catalog.list_tags())


def test_assign_multiple_tags(catalog: Catalog, tmp_path):
    lib = catalog.add_library(tmp_path)
    img = make_jpeg(tmp_path / "y.jpg")
    pid = _add_photo(catalog, lib.id, img)
    cat = catalog.list_categories()[0]
    t1 = catalog.create_tag("A", cat.id)
    t2 = catalog.create_tag("B", cat.id)
    catalog.set_photo_tags(pid, [t1.id, t2.id])
    names = sorted(t.name for t in catalog.get_photo_tags(pid))
    assert names == ["A", "B"]


def test_search_by_tag(catalog: Catalog, tmp_path):
    lib = catalog.add_library(tmp_path)
    p1 = _add_photo(catalog, lib.id, make_jpeg(tmp_path / "1.jpg"))
    p2 = _add_photo(catalog, lib.id, make_jpeg(tmp_path / "2.jpg"))
    cat = catalog.list_categories()[0]
    t = catalog.create_tag("Special", cat.id)
    catalog.add_tag_to_photo(p1, t.id)

    results = catalog.search(PhotoQuery(tag_ids=[t.id]))
    assert [p.id for p in results] == [p1]

    untagged = catalog.search(PhotoQuery(untagged=True))
    assert [p.id for p in untagged] == [p2]


def test_search_by_text(catalog: Catalog, tmp_path):
    lib = catalog.add_library(tmp_path)
    p1 = _add_photo(catalog, lib.id, make_jpeg(tmp_path / "tram.jpg"))
    _add_photo(catalog, lib.id, make_jpeg(tmp_path / "bus.jpg"))
    results = catalog.search(PhotoQuery(text="tram"))
    assert [p.id for p in results] == [p1]


def test_mark_missing(catalog: Catalog, tmp_path):
    lib = catalog.add_library(tmp_path)
    a = make_jpeg(tmp_path / "a.jpg")
    b = make_jpeg(tmp_path / "b.jpg")
    _add_photo(catalog, lib.id, a)
    _add_photo(catalog, lib.id, b)
    newly = catalog.mark_missing(lib.id, {str(a)})
    assert newly == 1
    missing = catalog.search(PhotoQuery(missing=True))
    assert [p.file_name for p in missing] == ["b.jpg"]


def test_search_by_text_matches_tags_case_insensitive(catalog: Catalog, tmp_path):
    """Cyrillic search must be case-insensitive (SQLite LIKE alone is not)."""
    lib = catalog.add_library(tmp_path)
    tagged = _add_photo(catalog, lib.id, make_jpeg(tmp_path / "one.jpg"))
    _add_photo(catalog, lib.id, make_jpeg(tmp_path / "two.jpg"))
    category = catalog.list_categories()[0]
    tag = catalog.create_tag("Трамвай", category.id)
    catalog.add_tag_to_photo(tagged, tag.id)

    results = catalog.search(PhotoQuery(text="трам"))
    assert [p.id for p in results] == [tagged]


def test_search_matches_camera_model(catalog: Catalog, tmp_path):
    lib = catalog.add_library(tmp_path)
    p1 = _add_photo(
        catalog, lib.id, make_jpeg(tmp_path / "cam.jpg"),
        camera_model="Canon EOS 5D",
    )
    _add_photo(catalog, lib.id, make_jpeg(tmp_path / "other.jpg"),
               camera_model="Nikon Z6")
    results = catalog.search(PhotoQuery(text="canon"))
    assert [p.id for p in results] == [p1]


def test_relocation_ignores_present_photos(catalog: Catalog, tmp_path):
    from core import identification

    lib = catalog.add_library(tmp_path)
    img = make_jpeg(tmp_path / "here.jpg", color=(5, 5, 5))
    pid = _add_photo(
        catalog, lib.id, img,
        content_hash=identification.partial_hash(img),
        size_bytes=img.stat().st_size,
    )
    # The photo is present, so it must not be treated as a relocation target.
    found = catalog.find_relocation(
        lib.id, instance_id=None, file_name=img.name,
        size=img.stat().st_size, content_hash=identification.partial_hash(img),
    )
    assert found is None
    assert catalog.get_photo(pid).is_missing is False


def test_mark_present_clears_flag(catalog: Catalog, tmp_path):
    lib = catalog.add_library(tmp_path)
    pid = _add_photo(catalog, lib.id, make_jpeg(tmp_path / "a.jpg"))
    catalog.mark_missing(lib.id, set())
    assert catalog.get_photo(pid).is_missing is True
    catalog.mark_present(pid)
    assert catalog.get_photo(pid).is_missing is False


def test_relocation_by_hash(catalog: Catalog, tmp_path):
    lib = catalog.add_library(tmp_path)
    original = make_jpeg(tmp_path / "old" / "img.jpg", color=(33, 66, 99))
    pid = _add_photo(catalog, lib.id, original,
                     content_hash=identification.partial_hash(original),
                     size_bytes=original.stat().st_size)
    # "Move" the file and mark old path missing.
    catalog.mark_missing(lib.id, set())
    moved = tmp_path / "new" / "img.jpg"
    moved.parent.mkdir(parents=True)
    original.replace(moved)

    found = catalog.find_relocation(
        lib.id, instance_id=None, file_name=moved.name,
        size=moved.stat().st_size, content_hash=identification.partial_hash(moved),
    )
    assert found == pid


def test_find_relocation_with_present_paths_reconnects_in_same_pass(
    catalog: Catalog, tmp_path
):
    """A row whose path is absent from the current scan is a move target.

    Unlike the missing-flag path, this must work while the old row is still
    ``is_missing = 0`` (i.e. before ``mark_missing`` runs at scan end).
    """
    lib = catalog.add_library(tmp_path)
    old = tmp_path / "a" / "img.jpg"
    make_jpeg(old, color=(33, 66, 99))
    pid = _add_photo(
        catalog, lib.id, old,
        content_hash=identification.partial_hash(old),
        size_bytes=old.stat().st_size,
    )

    new = tmp_path / "b" / "img.jpg"
    new.parent.mkdir(parents=True)
    old.rename(new)
    present = {str(new)}

    found = catalog.find_relocation(
        lib.id, instance_id=None, file_name=new.name,
        size=new.stat().st_size, content_hash=identification.partial_hash(new),
        present_paths=present,
    )
    assert found == pid


def test_find_relocation_present_paths_skips_still_present_row(
    catalog: Catalog, tmp_path
):
    """A row whose path *is* in the current scan must not be hijacked."""
    lib = catalog.add_library(tmp_path)
    img = make_jpeg(tmp_path / "here.jpg", color=(5, 5, 5))
    pid = _add_photo(
        catalog, lib.id, img,
        content_hash=identification.partial_hash(img),
        size_bytes=img.stat().st_size,
    )
    found = catalog.find_relocation(
        lib.id, instance_id=None, file_name=img.name,
        size=img.stat().st_size, content_hash=identification.partial_hash(img),
        present_paths={str(img)},
    )
    assert found is None
    assert catalog.get_photo(pid).is_missing is False


def test_merge_duplicate_photos_keeps_tags(catalog: Catalog, tmp_path):
    """Duplicate rows for one image collapse into one, keeping the tags."""
    lib = catalog.add_library(tmp_path)
    img = make_jpeg(tmp_path / "204.tif", color=(1, 2, 3))
    content_hash = identification.partial_hash(img)
    size = img.stat().st_size

    # Original row: missing but carries the tags (the historical bug's result).
    tagged = _add_photo(
        catalog, lib.id, str(tmp_path / "old" / "204.tif"),
        content_hash=content_hash, size_bytes=size,
    )
    category = catalog.list_categories()[0]
    tag = catalog.create_tag("204", category.id)
    catalog.add_tag_to_photo(tagged, tag.id)
    catalog.mark_missing(lib.id, set())

    # Fresh row at the real location: present but untagged.
    fresh = _add_photo(
        catalog, lib.id, str(img),
        content_hash=content_hash, size_bytes=size,
    )

    removed = catalog.merge_duplicate_photos()
    assert removed == 1
    assert catalog.count_photos(lib.id) == 1

    survivor = catalog.search(PhotoQuery())[0]
    assert survivor.id == fresh  # the present row wins
    assert Path(survivor.path) == img.resolve()
    assert [t.name for t in catalog.get_photo(survivor.id).tags] == ["204"]
    assert survivor.is_missing is False


def test_merge_duplicate_photos_is_idempotent(catalog: Catalog, tmp_path):
    lib = catalog.add_library(tmp_path)
    _add_photo(catalog, lib.id, make_jpeg(tmp_path / "a.jpg"))
    _add_photo(catalog, lib.id, make_jpeg(tmp_path / "b.jpg"))
    assert catalog.merge_duplicate_photos() == 0
    assert catalog.merge_duplicate_photos() == 0
    assert catalog.count_photos(lib.id) == 2