"""Photo information panel: metadata + editable tags for the selected photo."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core import paths
from core.catalog import Catalog
from core.models import Photo, Tag

from .tag_editor import TagAssignDialog

_PREVIEW_MAX_HEIGHT = 220


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
        self.preview.setMinimumHeight(140)
        self.preview.setMaximumHeight(_PREVIEW_MAX_HEIGHT)
        self.preview.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self.preview.setCursor(Qt.CursorShape.PointingHandCursor)
        self.preview.setToolTip("Открыть фотографию")
        self.preview.setText("Нет миниатюры")
        self.preview.clicked.connect(self._on_preview_clicked)
        root.addWidget(self.preview)

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
        details_layout.setSpacing(10)

        details_layout.addWidget(self._section_label("СВОЙСТВА"))
        self.meta_form = QFormGrid()
        details_layout.addWidget(self.meta_form)

        tags_header = QHBoxLayout()
        tags_header.addWidget(self._section_label("ТЕГИ"))
        tags_header.addStretch(1)
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
        self._clear_tags()
        self._set_enabled(False)

    def _set_enabled(self, enabled: bool) -> None:
        for widget in (self.add_tag_btn, self.open_btn, self.explorer_btn):
            widget.setEnabled(enabled)

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
        self._source_pixmap = None
        if photo.thumb_name:
            thumb_path = paths.thumbnail_cache_dir() / photo.thumb_name
            if thumb_path.is_file():
                pix = QPixmap(str(thumb_path))
                if not pix.isNull():
                    self._source_pixmap = pix
        if self._source_pixmap is None:
            self.preview.setPixmap(QPixmap())
            self.preview.setText("Нет миниатюры")
            return
        self.preview.setText("")
        self._render_preview()

    def _render_preview(self) -> None:
        """Scale the stored thumbnail to the current panel width."""
        if self._source_pixmap is None:
            return
        width = max(120, self.preview.width() - 4)
        height = max(120, min(_PREVIEW_MAX_HEIGHT, self.preview.height() - 4))
        self.preview.setPixmap(
            self._source_pixmap.scaled(
                width,
                height,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

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