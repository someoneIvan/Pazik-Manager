"""Offscreen smoke tests for the PyQt6 interface.

These construct real widgets (no ``exec`` loops) and exercise the wiring that
previously had gaps: gallery selection, zoom, viewer creation, filter defaults
and tag dialogs.
"""

from __future__ import annotations

import os

import pytest

# Must be set before QApplication is created.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from core.catalog import Catalog, PhotoQuery  # noqa: E402
from core.scanner import Scanner  # noqa: E402
from tests.conftest import make_jpeg  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def populated(tmp_path):
    catalog = Catalog(tmp_path / "ui.db")
    lib = catalog.add_library(tmp_path / "photos")
    (tmp_path / "photos").mkdir(parents=True, exist_ok=True)
    make_jpeg(tmp_path / "photos" / "a.jpg", exif_datetime="2020:01:02 03:04:05")
    make_jpeg(tmp_path / "photos" / "b.jpg")
    Scanner(catalog).scan_library(lib)
    yield catalog
    catalog.close()


def test_main_window_smoke(qapp, populated):
    from ui.main_window import MainWindow

    window = MainWindow(populated)
    window._refresh_gallery()
    assert window.gallery.photo_count() == 2
    assert len(window._photos) == 2

    window.gallery.select_photo_id(window._photos[0].id)
    assert window.gallery.selected_photo_id() is not None
    assert window.photo_info.title.text() == "a.jpg"

    # Zoom slider resizes gallery cells; icons stay 4:3.
    window.zoom_slider.setValue(300)
    assert window.gallery.icon_size() == 300
    icon = window.gallery.iconSize()
    assert abs(icon.width() / icon.height() - 4 / 3) < 0.01
    window.zoom_slider.setValue(200)

    window.close()


def test_empty_state_visible_without_photos(qapp, tmp_path):
    from ui.main_window import MainWindow

    catalog = Catalog(tmp_path / "empty.db")
    window = MainWindow(catalog)
    window._refresh_gallery()
    assert window.gallery.isHidden()
    assert not window.empty_label.isHidden()
    window.close()


def test_filter_date_defaults(qapp):
    from ui.filters import FilterPanel

    panel = FilterPanel()
    assert panel.values()["date_from"] is None
    assert panel.values()["date_to"] is None

    panel.enable_from.setChecked(True)
    panel.enable_to.setChecked(True)
    # Enabling "from" must not silently filter to the year 2000.
    assert panel.values()["date_from"] == "1990-01-01"
    assert panel.values()["date_to"] is not None

    panel.reset()
    assert panel.values()["date_from"] is None


def test_viewer_navigation(qapp, populated):
    from ui.viewer import ImageViewer

    photos = populated.search(PhotoQuery())
    viewer = ImageViewer(photos, 0)
    assert viewer.counter.text() == "1 / 2"
    viewer.navigate(1)
    assert viewer.counter.text() == "2 / 2"
    viewer.navigate(1)  # clamp at the end
    assert viewer.counter.text() == "2 / 2"
    viewer.close()


def test_tag_dialogs_construct(qapp, populated):
    from ui.tag_editor import TagAssignDialog, TagManagerDialog

    photo = populated.search(
        PhotoQuery()
    )[0]
    assign = TagAssignDialog(populated, photo)
    assert assign.windowTitle() == "Назначить теги"

    manager = TagManagerDialog(populated)
    assert manager.windowTitle() == "Менеджер тегов"

    assign.close()
    manager.close()


def test_gallery_groups_by_date(qapp, populated):
    from ui.gallery import _group_by_date

    photos = populated.search(PhotoQuery())
    groups = _group_by_date(photos)
    # a.jpg carries a 2020 date, b.jpg is undated -> undated group comes last.
    assert list(groups.keys())[-1] == "Без даты"
    assert sum(len(v) for v in groups.values()) == 2


def test_date_group_label_relative_days(qapp):
    import datetime as dt

    from ui.gallery import _date_group_label

    today = dt.date.today()
    assert _date_group_label(today.isoformat()) == "Сегодня"
    assert _date_group_label((today - dt.timedelta(days=1)).isoformat()) == "Вчера"
    assert _date_group_label(None) == "Без даты"
    assert _date_group_label("not-a-date") == "Без даты"


def test_photo_info_shows_and_clears_geotag(qapp, populated):
    from ui.photo_info import PhotoInfo

    photo = populated.search(PhotoQuery())[0]
    populated.set_photo_location(photo.id, 55.7558, 37.6173)
    panel = PhotoInfo(populated)
    panel.set_photo(populated.get_photo(photo.id))

    assert "55.755800" in panel.geo_coords.text()
    assert "37.617300" in panel.geo_coords.text()
    assert panel.geo_source.text() == "вручную"
    assert panel.geo_edit_btn.text() == "Изменить"
    assert not panel.geo_dms.isHidden()

    panel._on_clear_geo()
    assert panel.geo_coords.text() == "Геометка не задана"
    assert panel.geo_edit_btn.text() == "Указать"
    assert populated.get_photo(photo.id).latitude is None
    panel.close()


def test_geo_dialog_fills_fields_from_link(qapp):
    from ui.geo_dialog import GeoTagDialog

    dialog = GeoTagDialog()
    dialog.paste_edit.setText(
        "https://www.google.com/maps/@55.7558,37.6173,15z"
    )
    assert dialog.lat_edit.text() == "55.755800"
    assert dialog.lon_edit.text() == "37.617300"
    dialog.close()


def test_tag_assign_preserves_unchecked_state(qapp, populated):
    from ui.tag_editor import TagAssignDialog

    photo = populated.search(
        PhotoQuery()
    )[0]
    category = populated.list_categories()[0]
    tag_a = populated.create_tag("A", category.id)
    tag_b = populated.create_tag("B", category.id)
    populated.add_tag_to_photo(photo.id, tag_a.id)

    dialog = TagAssignDialog(populated, photo)
    dialog._checks[tag_a.id].setChecked(False)
    # Rebuilding (e.g. after creating a new tag) must not re-check "A".
    dialog._rebuild(preserve=dialog._selected_ids() | {tag_b.id})
    assert dialog._checks[tag_a.id].isChecked() is False
    assert dialog._checks[tag_b.id].isChecked() is True
    dialog.close()