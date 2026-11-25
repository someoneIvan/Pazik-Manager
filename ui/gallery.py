"""Gallery: a responsive grid of photo thumbnails, grouped by shooting date."""

from __future__ import annotations

import datetime as _dt
from collections import OrderedDict
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
_HEADER_HEIGHT = 34
_ICON_CACHE_LIMIT = 3000

#: Russian month names (nominative → genitive) for date-group headers.
_MONTHS = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


def _cell_size(edge: int) -> QSize:
    """Return the 4:3 icon size for a given cell width."""
    return QSize(int(edge), round(edge / THUMB_ASPECT))


def _parse_iso_date(value: Optional[str]) -> Optional[_dt.date]:
    if not value:
        return None
    try:
        return _dt.date.fromisoformat(value[:10])
    except ValueError:
        return None


def _date_group_label(value: Optional[str]) -> str:
    """Human label for a shooting-date group, Windows-Explorer style.

    ``Сегодня`` / ``Вчера`` for the last two days, otherwise ``24 ноября`` (this
    year) or ``24 ноября 2024``.  Undated photos fall into ``Без даты``.
    """
    day = _parse_iso_date(value)
    if day is None:
        return "Без даты"
    today = _dt.date.today()
    if day == today:
        return "Сегодня"
    if day == today - _dt.timedelta(days=1):
        return "Вчера"
    month = _MONTHS[day.month - 1]
    if day.year == today.year:
        return f"{day.day} {month}"
    return f"{day.day} {month} {day.year}"


def _group_by_date(photos: list[Photo]) -> "OrderedDict[str, list[Photo]]":
    """Group photos by their shooting-date label, preserving input order."""
    groups: "OrderedDict[str, list[Photo]]" = OrderedDict()
    for photo in photos:
        groups.setdefault(_date_group_label(photo.date_taken), []).append(photo)
    return groups

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
        # Variable item sizes: photo cells share a size, but date-group header
        # rows are full-width and must be allowed to differ.
        self.setUniformItemSizes(False)
        self.setWordWrap(False)
        self.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.setSpacing(4)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        self._placeholder = _placeholder_icon()
        self._photo_ids: list[int] = []
        self._icon_cache: dict[str, QIcon] = {}
        self._photo_size_hint = QSize(0, 0)

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
        self._photo_size_hint = QSize(edge + 20, cell_h + _TEXT_HEIGHT + 16)
        self._relayout_items()

    def icon_size(self) -> int:
        return getattr(self, "_icon_size", DEFAULT_ICON_SIZE)

    def _relayout_items(self) -> None:
        """Re-apply size hints after a zoom change or resize.

        Photo rows keep the cell size; header rows span the full viewport width
        so they always occupy their own line and force the grid to wrap.
        """
        width = self.viewport().width() or self.width()
        header_width = max(width, 1)
        photo_hint = self._photo_size_hint if self._photo_size_hint.width() else None
        for i in range(self.count()):
            item = self.item(i)
            if item.data(Qt.ItemDataRole.UserRole) is None:
                item.setSizeHint(QSize(header_width, _HEADER_HEIGHT))
            elif photo_hint is not None:
                item.setSizeHint(photo_hint)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        self._relayout_items()

    # -- population --------------------------------------------------------
    def set_photos(self, photos: list[Photo], keep_id: Optional[int] = None) -> None:
        """Replace the gallery contents, grouped by date, restoring selection."""
        if keep_id is None:
            keep_id = self.selected_photo_id()

        self.setUpdatesEnabled(False)
        try:
            self.clear()
            self._photo_ids = []
            width = self.viewport().width() or self.width()
            header_width = max(width, 1)
            photo_hint = self._photo_size_hint

            for label, group in _group_by_date(photos).items():
                header = QListWidgetItem(label)
                header.setData(Qt.ItemDataRole.UserRole, None)
                # Enabled but not selectable/focusable: a pure separator row.
                header.setFlags(Qt.ItemFlag.ItemIsEnabled)
                header.setTextAlignment(
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
                )
                header.setForeground(QColor("#9aa0aa"))
                font = header.font()
                font.setBold(True)
                header.setFont(font)
                if photo_hint.width():
                    header.setSizeHint(QSize(header_width, _HEADER_HEIGHT))
                self.addItem(header)

                for photo in group:
                    item = QListWidgetItem(self._icon_for(photo), photo.file_name)
                    item.setData(Qt.ItemDataRole.UserRole, photo.id)
                    item.setToolTip(self._tooltip(photo))
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom
                    )
                    if photo_hint.width():
                        item.setSizeHint(photo_hint)
                    self.addItem(item)
                    self._photo_ids.append(photo.id)
        finally:
            self.setUpdatesEnabled(True)

        if keep_id is not None and keep_id in self._photo_ids:
            self.select_photo_id(keep_id)

    def select_photo_id(self, photo_id: int) -> None:
        """Select the row belonging to *photo_id* (skipping group headers)."""
        for i in range(self.count()):
            item = self.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == photo_id:
                self.setCurrentItem(item)
                return

    def photo_count(self) -> int:
        """Number of photos in the gallery (excludes date-group headers)."""
        return len(self._photo_ids)

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