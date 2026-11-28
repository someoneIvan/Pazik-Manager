"""Photo information panel: metadata + editable tags for the selected photo."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QSize, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QImage, QImageReader, QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core import paths, thumbnail
from core.catalog import Catalog
from core.geo import format_decimal, format_dms, is_valid, osm_url
from core.models import RASTER_EXTENSIONS, Photo, Tag

from . import icons
from .geo_dialog import GeoTagDialog
from .tag_editor import TagAssignDialog

_PREVIEW_MIN_HEIGHT = 120
_PREVIEW_MAX_HEIGHT = 320
#: Longest edge (px) the full preview is decoded at, to bound memory use.
_PREVIEW_MAX_EDGE = 2048


class ClickableLabel(QLabel):
    """A QLabel that emits :attr:`clicked` on a mouse press."""

    clicked = pyqtSignal()

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class PhotoInfo(QWidget):
    """Right-hand panel showing details of the current photo."""

    tags_changed = pyqtSignal()
    open_requested = pyqtSignal(int)
    explorer_requested = pyqtSignal(int)

    def __init__(self, catalog: Catalog, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.catalog = catalog
        self._photo: Optional[Photo] = None
        self._source_pixmap: Optional[QPixmap] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        self.title = QLabel("Фотография не выбрана")
        self.title.setObjectName("PhotoTitle")
        self.title.setWordWrap(True)
        root.addWidget(self.title)

        self.preview = ClickableLabel()
        self.preview.setObjectName("Preview")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(_PREVIEW_MIN_HEIGHT)
        self.preview.setMaximumHeight(_PREVIEW_MAX_HEIGHT)
        self.preview.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self.preview.setCursor(Qt.CursorShape.PointingHandCursor)
        self.preview.setToolTip("Открыть фотографию")
        self.preview.setText("Нет миниатюры")
        self.preview.clicked.connect(self._on_preview_clicked)
        # Centre the frame; its size is set to the image's exact size so the
        # border hugs the photo (no letterbox bars around it).
        root.addWidget(self.preview, 0, Qt.AlignmentFlag.AlignHCenter)

        # Action buttons
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.open_btn = QPushButton("Открыть")
        self.open_btn.setObjectName("Primary")
        self.open_btn.clicked.connect(self._on_preview_clicked)
        actions.addWidget(self.open_btn)
        self.explorer_btn = QPushButton("В папке")
        self.explorer_btn.setObjectName("Ghost")
        self.explorer_btn.clicked.connect(self._on_explorer_clicked)
        actions.addWidget(self.explorer_btn)
        actions.addStretch(1)
        root.addLayout(actions)

        # Scrollable details
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        details = QWidget()
        details_layout = QVBoxLayout(details)
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.setSpacing(8)
        # Keep the scroll widget at least as tall as its contents; otherwise
        # QScrollArea squeezes the cards when the viewport is short.
        details_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)

        # --- geotag -------------------------------------------------------
        self.geo_card = QFrame()
        self.geo_card.setObjectName("InfoCard")
        geo_layout = QVBoxLayout(self.geo_card)
        geo_layout.setContentsMargins(12, 10, 12, 10)
        geo_layout.setSpacing(6)

        geo_header = self._section_header(icons.pin_icon(), "ГЕОМЕТКА")
        self.geo_source = QLabel("")
        self.geo_source.setObjectName("Muted")
        geo_header.addWidget(self.geo_source)
        geo_layout.addLayout(geo_header)

        self.geo_coords = QLabel("—")
        self.geo_coords.setObjectName("GeoCoords")
        self.geo_coords.setWordWrap(True)
        self.geo_coords.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        geo_layout.addWidget(self.geo_coords)

        self.geo_dms = QLabel("")
        self.geo_dms.setObjectName("GeoDms")
        self.geo_dms.setWordWrap(True)
        self.geo_dms.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.geo_dms.setVisible(False)
        geo_layout.addWidget(self.geo_dms)

        geo_actions = QHBoxLayout()
        geo_actions.setSpacing(6)
        self.geo_map_btn = QPushButton("На карте")
        self.geo_map_btn.setObjectName("Ghost")
        self.geo_map_btn.setIcon(icons.pin_icon())
        self.geo_map_btn.setIconSize(QSize(14, 14))
        self.geo_map_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.geo_map_btn.setToolTip("Открыть точку в OpenStreetMap")
        self.geo_map_btn.clicked.connect(self._on_open_map)
        geo_actions.addWidget(self.geo_map_btn)
        geo_actions.addStretch(1)
        self.geo_edit_btn = QPushButton("Указать")
        self.geo_edit_btn.setObjectName("Primary")
        self.geo_edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.geo_edit_btn.clicked.connect(self._on_edit_geo)
        geo_actions.addWidget(self.geo_edit_btn)
        self.geo_clear_btn = QPushButton()
        self.geo_clear_btn.setObjectName("ChipRemove")
        self.geo_clear_btn.setIcon(icons.trash_icon())
        self.geo_clear_btn.setIconSize(QSize(14, 14))
        self.geo_clear_btn.setFixedSize(26, 26)
        self.geo_clear_btn.setToolTip("Удалить геометку")
        self.geo_clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.geo_clear_btn.clicked.connect(self._on_clear_geo)
        geo_actions.addWidget(self.geo_clear_btn)
        geo_layout.addLayout(geo_actions)
        details_layout.addWidget(self.geo_card)

        # --- properties ---------------------------------------------------
        details_layout.addLayout(
            self._section_header(icons.camera_icon(), "СВОЙСТВА")
        )
        self.meta_form = QFormGrid()
        meta_card = QFrame()
        meta_card.setObjectName("InfoCard")
        meta_layout = QVBoxLayout(meta_card)
        meta_layout.setContentsMargins(12, 10, 12, 10)
        meta_layout.setSpacing(0)
        meta_layout.addWidget(self.meta_form)
        details_layout.addWidget(meta_card)

        # --- tags ---------------------------------------------------------
        tags_header = self._section_header(icons.tag_icon(), "ТЕГИ")
        self.add_tag_btn = QPushButton("+ Тег")
        self.add_tag_btn.setObjectName("Ghost")
        self.add_tag_btn.clicked.connect(self._on_add_tag)
        tags_header.addWidget(self.add_tag_btn)
        details_layout.addLayout(tags_header)

        self.tags_container = QWidget()
        self.tags_layout = QVBoxLayout(self.tags_container)
        self.tags_layout.setContentsMargins(0, 0, 0, 0)
        self.tags_layout.setSpacing(6)
        self.tags_layout.addStretch(1)
        details_layout.addWidget(self.tags_container)

        details_layout.addStretch(1)
        scroll.setWidget(details)
        root.addWidget(scroll, 1)

        self._set_enabled(False)

    # -- public API --------------------------------------------------------
    def set_photo(self, photo: Optional[Photo]) -> None:
        self._photo = photo
        if photo is None:
            self._clear()
            return
        self._set_enabled(True)
        self.title.setText(photo.file_name)
        self.title.setToolTip(photo.path)
        self._fill_metadata(photo)
        self._fill_preview(photo)
        self._fill_geo(photo)
        self._fill_tags(photo.tags)

    def refresh_tags(self, photo: Optional[Photo] = None) -> None:
        photo = photo or self._photo
        if photo is None:
            return
        photo.tags = self.catalog.get_photo_tags(photo.id)
        self._fill_tags(photo.tags)

    # -- layout events -----------------------------------------------------
    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        self._render_preview()

    # -- internals ---------------------------------------------------------
    def _clear(self) -> None:
        self.title.setText("Фотография не выбрана")
        self.title.setToolTip("")
        self._source_pixmap = None
        self.preview.setPixmap(QPixmap())
        self.preview.setText("Нет миниатюры")
        self.meta_form.clear()
        self._fill_geo(None)
        self._clear_tags()
        self._set_enabled(False)

    def _set_enabled(self, enabled: bool) -> None:
        for widget in (self.add_tag_btn, self.open_btn, self.explorer_btn):
            widget.setEnabled(enabled)
        self._update_geo_actions()

    def _fill_metadata(self, photo: Photo) -> None:
        self.meta_form.clear()
        camera = " ".join(filter(None, [photo.camera_make, photo.camera_model]))
        rows = [
            ("Разрешение", photo.dimensions),
            ("Дата съёмки", self._fmt_date(photo.date_taken)),
            ("Камера", camera),
            ("Объектив", photo.lens or ""),
            ("ISO", photo.iso or ""),
            ("Диафрагма", photo.aperture or ""),
            ("Выдержка", photo.shutter or ""),
            ("Фокусное", photo.focal_length or ""),
            ("Размер", self._human_size(photo.size_bytes)),
            ("Формат", photo.ext.upper() if photo.ext else ""),
        ]
        for label, value in rows:
            self.meta_form.add_row(label, self._value_label(value))

        # The path is shown relative to its library root to keep it readable;
        # the absolute path stays available in the tooltip.
        path_label = self._value_label(
            self._breakable_path(self._display_path(photo)), color="#a8adb6"
        )
        path_label.setToolTip(photo.path)
        self.meta_form.add_row("Путь", path_label)

        if photo.is_missing:
            self.meta_form.add_row(
                "", self._value_label("⚠ Файл отсутствует", color="#ff5c5c")
            )

    def _fill_preview(self, photo: Photo) -> None:
        # Prefer the *full* (uncropped) image so the whole photo is visible;
        # the cached thumbnail is centre-cropped to 4:3 and would cut off edges.
        self._source_pixmap = self._load_full_source(photo)
        if self._source_pixmap is None:
            self._source_pixmap = self._load_thumbnail_pixmap(photo)
        if self._source_pixmap is None:
            self.preview.setPixmap(QPixmap())
            self.preview.setText("Нет миниатюры")
            return
        self.preview.setText("")
        self._render_preview()

    def _load_full_source(self, photo: Photo) -> Optional[QPixmap]:
        """Load the whole image (not the 4:3 crop), capped to a sane size."""
        if photo.ext in RASTER_EXTENSIONS:
            try:
                reader = QImageReader(photo.path)
                reader.setAutoTransform(True)
                size = reader.size()
                if size.isValid() and size.width() > 0 and size.height() > 0:
                    longest = max(size.width(), size.height())
                    if longest > _PREVIEW_MAX_EDGE:
                        scale = _PREVIEW_MAX_EDGE / longest
                        reader.setScaledSize(
                            QSize(
                                round(size.width() * scale),
                                round(size.height() * scale),
                            )
                        )
                image = reader.read()
                if not image.isNull():
                    return QPixmap.fromImage(image)
            except Exception:  # pragma: no cover - defensive
                pass
            return None

        # RAW: Qt cannot decode it, so use the camera's embedded preview.
        data = thumbnail.preview_bytes(photo.path)
        if not data:
            return None
        image = QImage.fromData(data)
        if image.isNull():
            return None
        return QPixmap.fromImage(self._cap_image(image))

    def _load_thumbnail_pixmap(self, photo: Photo) -> Optional[QPixmap]:
        """Fallback to the cached (4:3) thumbnail when the full image is absent."""
        if photo.thumb_name:
            thumb_path = paths.thumbnail_cache_dir() / photo.thumb_name
            if thumb_path.is_file():
                pix = QPixmap(str(thumb_path))
                if not pix.isNull():
                    return pix
        return None

    @staticmethod
    def _cap_image(image: QImage) -> QImage:
        """Downscale *image* so its longest edge is <= :data:`_PREVIEW_MAX_EDGE`."""
        longest = max(image.width(), image.height())
        if longest <= _PREVIEW_MAX_EDGE or longest <= 0:
            return image
        scale = _PREVIEW_MAX_EDGE / longest
        return image.scaled(
            round(image.width() * scale),
            round(image.height() * scale),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    def _render_preview(self) -> None:
        """Size the frame to the image itself, so its border hugs the photo.

        The image is scaled to fit within the panel width and
        ``_PREVIEW_MAX_HEIGHT`` (keeping its aspect), and the label is resized to
        the *scaled image's* exact size — no empty margins around it.  A tall
        portrait becomes a tall frame, a panorama a short one.
        """
        if self._source_pixmap is None:
            return
        # Available width is the panel width minus the root layout margins (16 px
        # each side); the label itself shrinks to the image, so use the panel.
        avail_w = max(120, self.width() - 32)
        scaled = self._source_pixmap.scaled(
            avail_w,
            _PREVIEW_MAX_HEIGHT,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.preview.setFixedSize(scaled.size())
        self.preview.setPixmap(scaled)

    def _fill_tags(self, tags: list[Tag]) -> None:
        self._clear_tags()
        if not tags:
            placeholder = QLabel("Теги не назначены")
            placeholder.setObjectName("Muted")
            self.tags_layout.insertWidget(0, placeholder)
            return
        categories = {c.id: c.name for c in self.catalog.list_categories()}
        for tag in tags:
            self.tags_layout.insertWidget(
                self.tags_layout.count() - 1,
                self._tag_row(tag, categories.get(tag.category_id, "")),
            )

    def _tag_row(self, tag: Tag, category_name: str) -> QWidget:
        row = QFrame()
        row.setObjectName("TagChip")
        row.setStyleSheet(
            "QFrame#TagChip {"
            "  background-color: rgba(255,255,255,0.04);"
            "  border: 1px solid rgba(255,255,255,0.05);"
            f" border-left: 3px solid {tag.color};"
            "  border-radius: 8px;"
            "}"
        )
        if category_name:
            row.setToolTip(category_name)

        h = QHBoxLayout(row)
        h.setContentsMargins(10, 5, 6, 5)
        h.setSpacing(8)
        name = QLabel(tag.name)
        name.setWordWrap(True)
        h.addWidget(name, 1)
        remove = QPushButton("✕")
        remove.setObjectName("ChipRemove")
        remove.setFixedSize(24, 24)
        remove.setToolTip("Убрать тег")
        remove.clicked.connect(lambda _=False, t=tag: self._on_remove_tag(t))
        h.addWidget(remove)
        return row

    # -- geotag ------------------------------------------------------------
    def _geo_coordinates(self) -> Optional[tuple[float, float]]:
        photo = self._photo
        if photo is None:
            return None
        if not is_valid(photo.latitude, photo.longitude):
            return None
        return float(photo.latitude), float(photo.longitude)

    def _fill_geo(self, photo: Optional[Photo]) -> None:
        coords = self._geo_coordinates() if photo is not None else None
        if coords is None:
            self.geo_coords.setText(
                "Геометка не задана" if photo is not None else "—"
            )
            self.geo_dms.clear()
            self.geo_dms.setVisible(False)
            self.geo_source.clear()
        else:
            latitude, longitude = coords
            self.geo_coords.setText(format_decimal(latitude, longitude))
            self.geo_dms.setText(format_dms(latitude, longitude))
            self.geo_dms.setVisible(True)
            self.geo_source.setText(
                "вручную" if photo is not None and photo.geo_source == "manual"
                else "EXIF"
            )
        placeholder = coords is None
        self.geo_coords.setProperty("placeholder", placeholder)
        self.geo_coords.style().unpolish(self.geo_coords)
        self.geo_coords.style().polish(self.geo_coords)
        self._update_geo_actions()

    def _update_geo_actions(self) -> None:
        has_photo = self._photo is not None
        has_geo = self._geo_coordinates() is not None
        self.geo_edit_btn.setEnabled(has_photo)
        self.geo_edit_btn.setText("Изменить" if has_geo else "Указать")
        self.geo_map_btn.setVisible(has_geo)
        self.geo_clear_btn.setVisible(has_geo)

    def _on_edit_geo(self) -> None:
        if self._photo is None:
            return
        coords = self._geo_coordinates()
        dialog = GeoTagDialog(
            coords[0] if coords else None,
            coords[1] if coords else None,
            self,
        )
        if not dialog.exec():
            return
        result = dialog.coordinates()
        if result is None:
            return
        latitude, longitude = result
        self.catalog.set_photo_location(self._photo.id, latitude, longitude)
        self._photo.latitude = latitude
        self._photo.longitude = longitude
        self._photo.geo_source = "manual"
        self._fill_geo(self._photo)

    def _on_clear_geo(self) -> None:
        if self._photo is None:
            return
        self.catalog.clear_photo_location(self._photo.id)
        self._photo.latitude = None
        self._photo.longitude = None
        self._photo.geo_source = "manual"
        self._fill_geo(self._photo)

    def _on_open_map(self) -> None:
        coords = self._geo_coordinates()
        if coords is None:
            return
        QDesktopServices.openUrl(QUrl(osm_url(*coords)))

    # -- actions -----------------------------------------------------------
    def _on_add_tag(self) -> None:
        if self._photo is None:
            return
        dialog = TagAssignDialog(self.catalog, self._photo, self)
        if dialog.exec():
            self.refresh_tags()
            self.tags_changed.emit()

    def _on_remove_tag(self, tag: Tag) -> None:
        if self._photo is None:
            return
        self.catalog.remove_tag_from_photo(self._photo.id, tag.id)
        self.refresh_tags()
        self.tags_changed.emit()

    def _on_preview_clicked(self) -> None:
        if self._photo is not None:
            self.open_requested.emit(self._photo.id)

    def _on_explorer_clicked(self) -> None:
        if self._photo is not None:
            self.explorer_requested.emit(self._photo.id)

    # -- helpers -----------------------------------------------------------
    def _clear_tags(self) -> None:
        # Remove everything except the trailing stretch item.
        for i in reversed(range(self.tags_layout.count() - 1)):
            item = self.tags_layout.takeAt(i)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    @staticmethod
    def _section_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("SectionTitle")
        return lbl

    @classmethod
    def _section_header(cls, icon, text: str) -> QHBoxLayout:
        """Icon + uppercase section title, with a trailing stretch."""
        row = QHBoxLayout()
        row.setSpacing(6)
        icon_label = QLabel()
        icon_label.setPixmap(icon.pixmap(QSize(14, 14)))
        row.addWidget(icon_label)
        row.addWidget(cls._section_label(text))
        row.addStretch(1)
        return row

    @staticmethod
    def _value_label(value: str, color: str = "#e6e8ec") -> QLabel:
        lbl = QLabel(value or "—")
        lbl.setWordWrap(True)
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lbl.setStyleSheet(f"color: {color};")
        return lbl

    @staticmethod
    def _fmt_date(value: Optional[str]) -> str:
        if not value:
            return ""
        return value.replace("T", " ")

    @staticmethod
    def _breakable_path(path: str) -> str:
        """Allow long paths to wrap inside the panel (zero-width break points)."""
        return path.replace("\\", "\\\u200b").replace("/", "/\u200b")

    def _display_path(self, photo: Photo) -> str:
        """Path relative to its library root when possible."""
        library = self.catalog.get_library(photo.library_id)
        if library:
            try:
                return str(Path(photo.path).relative_to(library.path))
            except ValueError:
                pass
        return photo.path

    @staticmethod
    def _human_size(value: Optional[int]) -> str:
        if not value:
            return ""
        size = float(value)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024:
                return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size:.1f} TB"


class QFormGrid(QWidget):
    """A compact two-column label/value grid used for photo metadata."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._grid = QVBoxLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(5)
        self._rows: list[QWidget] = []

    def add_row(self, label: str, value_widget: QWidget) -> None:
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        key = QLabel(label)
        key.setObjectName("Muted")
        key.setFixedWidth(100)
        key.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        h.addWidget(key)
        h.addWidget(value_widget, 1)
        self._grid.addWidget(row)
        self._rows.append(row)

    def clear(self) -> None:
        for row in self._rows:
            self._grid.removeWidget(row)
            row.setParent(None)
            row.deleteLater()
        self._rows.clear()