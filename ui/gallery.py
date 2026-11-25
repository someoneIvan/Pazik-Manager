"""Gallery: a responsive grid of photo thumbnails."""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QListWidget, QListWidgetItem, QMenu, QWidget

from core import paths
from core.models import Photo
from core.thumbnail import THUMB_ASPECT

DEFAULT_ICON_SIZE = 200
MIN_ICON_SIZE = 120
MAX_ICON_SIZE = 340
_TEXT_HEIGHT = 24
_ICON_CACHE_LIMIT = 3000


def _cell_size(edge: int) -> QSize:
    """Return the 4:3 icon size for a given cell width."""
    return QSize(int(edge), round(edge / THUMB_ASPECT))

# Context-menu action names emitted through :attr:`context_action`.
ACTION_OPEN = "open"
ACTION_EXPLORER = "explorer"
ACTION_COPY_PATH = "copy_path"


def _placeholder_icon() -> QIcon:
    """A neutral 4:3 placeholder shown when a thumbnail is unavailable."""
    width = 320
    height = round(width / THUMB_ASPECT)
    pix = QPixmap(width, height)
    pix.fill(QColor("#2a2d34"))
    painter = QPainter(pix)
    painter.setPen(QColor("#4a4f5a"))
    painter.drawRect(10, 10, width - 20, height - 20)
    painter.drawLine(10, 10, width - 10, height - 10)
    painter.drawLine(width - 10, 10, 10, height - 10)
    painter.end()
    return QIcon(pix)


class Gallery(QListWidget):
    """Icon-mode list that displays photo thumbnails in a flowing grid."""

    photo_selected = pyqtSignal(int)      # single click / keyboard selection
    photo_activated = pyqtSignal(int)     # double click / Enter
    context_action = pyqtSignal(str, int)  # action name, photo id

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("Gallery")
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setMovement(QListWidget.Movement.Static)
        self.setUniformItemSizes(True)
        self.setWordWrap(False)
        self.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.setSpacing(4)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        self._placeholder = _placeholder_icon()
        self._photo_ids: list[int] = []
        self._icon_cache: dict[str, QIcon] = {}

        self.set_icon_size(DEFAULT_ICON_SIZE)

        self.itemSelectionChanged.connect(self._on_selection_changed)
        # Double click only: ``itemActivated`` can also fire on single click on
        # some platforms, which would open the viewer unexpectedly.
        self.itemDoubleClicked.connect(self._on_activated)
        self.customContextMenuRequested.connect(self._show_context_menu)

    # -- appearance --------------------------------------------------------
    def set_icon_size(self, edge: int) -> None:
        """Resize gallery cells (called from the zoom slider).

        *edge* is the cell width; the icon itself is a 4:3 rectangle.
        """
        edge = max(MIN_ICON_SIZE, min(MAX_ICON_SIZE, int(edge)))
        self._icon_size = edge
        self.setIconSize(_cell_size(edge))
        cell_h = round(edge / THUMB_ASPECT)
        self.setGridSize(QSize(edge + 20, cell_h + _TEXT_HEIGHT + 16))

    def icon_size(self) -> int:
        return getattr(self, "_icon_size", DEFAULT_ICON_SIZE)

    # -- population --------------------------------------------------------
    def set_photos(self, photos: list[Photo], keep_id: Optional[int] = None) -> None:
        """Replace the gallery contents, optionally restoring the selection."""
        if keep_id is None:
            keep_id = self.selected_photo_id()

        self.setUpdatesEnabled(False)
        try:
            self.clear()
            self._photo_ids = []
            for photo in photos:
                item = QListWidgetItem(self._icon_for(photo), photo.file_name)
                item.setData(Qt.ItemDataRole.UserRole, photo.id)
                item.setToolTip(self._tooltip(photo))
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom
                )
                self.addItem(item)
                self._photo_ids.append(photo.id)
        finally:
            self.setUpdatesEnabled(True)

        if keep_id is not None and keep_id in self._photo_ids:
            row = self._photo_ids.index(keep_id)
            self.setCurrentRow(row)

    def _icon_for(self, photo: Photo) -> QIcon:
        if not photo.thumb_name:
            return self._placeholder

        cached = self._icon_cache.get(photo.thumb_name)
        if cached is not None:
            return cached

        thumb_path = paths.thumbnail_cache_dir() / photo.thumb_name
        if not thumb_path.is_file():
            return self._placeholder

        icon = QIcon(str(thumb_path))
        if icon.isNull():
            return self._placeholder

        if len(self._icon_cache) >= _ICON_CACHE_LIMIT:
            # Drop the oldest half; thumbnails can always be re-read from disk.
            for key in list(self._icon_cache)[: _ICON_CACHE_LIMIT // 2]:
                self._icon_cache.pop(key, None)
        self._icon_cache[photo.thumb_name] = icon
        return icon

    @staticmethod
    def _tooltip(photo: Photo) -> str:
        parts = [photo.file_name]
        if photo.dimensions:
            parts.append(photo.dimensions)
        if photo.date_taken:
            parts.append(photo.date_taken.replace("T", " "))
        if photo.camera_model:
            parts.append(photo.camera_model)
        if photo.is_missing:
            parts.append("— файл отсутствует")
        return "\n".join(parts)

    # -- selection ---------------------------------------------------------
    def selected_photo_id(self) -> Optional[int]:
        item = self.currentItem()
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def current_photo_ids(self) -> list[int]:
        return list(self._photo_ids)

    def photo_at(self, row: int) -> Optional[int]:
        if 0 <= row < len(self._photo_ids):
            return self._photo_ids[row]
        return None

    # -- signals -----------------------------------------------------------
    def _on_selection_changed(self) -> None:
        pid = self.selected_photo_id()
        if pid is not None:
            self.photo_selected.emit(pid)

    def _on_activated(self, item: QListWidgetItem) -> None:
        pid = item.data(Qt.ItemDataRole.UserRole)
        if pid is not None:
            self.photo_activated.emit(pid)

    def _show_context_menu(self, pos) -> None:
        item = self.itemAt(pos)
        if item is None:
            return
        pid = item.data(Qt.ItemDataRole.UserRole)
        if pid is None:
            return

        menu = QMenu(self)
        open_action = QAction("Открыть", menu)
        open_action.triggered.connect(
            lambda: self.context_action.emit(ACTION_OPEN, pid)
        )
        explorer_action = QAction("Показать в папке", menu)
        explorer_action.triggered.connect(
            lambda: self.context_action.emit(ACTION_EXPLORER, pid)
        )
        copy_action = QAction("Копировать путь", menu)
        copy_action.triggered.connect(
            lambda: self.context_action.emit(ACTION_COPY_PATH, pid)
        )
        menu.addAction(open_action)
        menu.addAction(explorer_action)
        menu.addSeparator()
        menu.addAction(copy_action)
        menu.exec(self.mapToGlobal(pos))