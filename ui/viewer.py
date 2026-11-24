"""Full-screen photo viewer with zoom, pan and keyboard navigation.

Raster files are loaded through :class:`QImageReader` (downscaled when extremely
large so memory stays bounded).  RAW files cannot be decoded by Qt, so their
embedded preview is extracted with ExifTool instead.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QImageReader,
    QKeySequence,
    QPainter,
    QPixmap,
    QShortcut,
)
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core import thumbnail
from core.models import RASTER_EXTENSIONS, Photo

#: Longest edge a raster image is decoded to.  Prevents multi-hundred-megabyte
#: pixmaps for very high resolution files while keeping detail for zooming.
_MAX_DECODE_EDGE = 6000
_ZOOM_STEP = 1.2
_MIN_SCALE = 0.05
_MAX_SCALE = 12.0


class ZoomView(QGraphicsView):
    """Graphics view with wheel zoom and drag-to-pan."""

    zoomed = pyqtSignal(int)  # zoom percentage

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._item: Optional[QGraphicsPixmapItem] = None
        self._scale = 1.0
        self.fit_mode = True

        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setBackgroundBrush(QColor("#0b0d10"))
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

    # -- content -----------------------------------------------------------
    def set_pixmap(self, pixmap: QPixmap) -> None:
        self._scene.clear()
        self._item = None
        if pixmap.isNull():
            self._emit_zoom()
            return
        self._item = self._scene.addPixmap(pixmap)
        self._scene.setSceneRect(self._item.boundingRect())
        self._scale = 1.0
        self.fit_to_window()

    def has_image(self) -> bool:
        return self._item is not None

    # -- zoom --------------------------------------------------------------
    def fit_to_window(self) -> None:
        if self._item is None:
            return
        self.fitInView(self._item, Qt.AspectRatioMode.KeepAspectRatio)
        self.fit_mode = True
        self._scale = self.transform().m11()
        self._emit_zoom()

    def actual_size(self) -> None:
        if self._item is None:
            return
        self.resetTransform()
        self.fit_mode = False
        self._scale = 1.0
        self.centerOn(self._item)
        self._emit_zoom()

    def zoom_by(self, factor: float) -> None:
        if self._item is None:
            return
        target = self._scale * factor
        if target < _MIN_SCALE or target > _MAX_SCALE:
            return
        self.scale(factor, factor)
        self._scale = target
        self.fit_mode = False
        self._emit_zoom()

    def zoom_percent(self) -> int:
        return round(self._scale * 100)

    def _emit_zoom(self) -> None:
        self.zoomed.emit(self.zoom_percent())

    # -- events ------------------------------------------------------------
    def wheelEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if not self.has_image():
            return
        delta = event.angleDelta().y()
        if delta:
            self.zoom_by(_ZOOM_STEP if delta > 0 else 1 / _ZOOM_STEP)
        event.accept()

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        if self.fit_mode:
            self.fit_to_window()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if self.fit_mode:
            self.actual_size()
        else:
            self.fit_to_window()


class ImageViewer(QDialog):
    """Modal viewer for one photo with next/previous navigation."""

    def __init__(
        self,
        photos: list[Photo],
        index: int = 0,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._photos = photos
        self._index = max(0, min(index, len(photos) - 1))

        self.setWindowTitle("Просмотр фотографии")
        self.resize(1200, 820)
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QWidget()
        header.setObjectName("ViewerHeader")
        header_l = QHBoxLayout(header)
        header_l.setContentsMargins(16, 10, 16, 10)
        self.title = QLabel()
        self.title.setObjectName("ViewerTitle")
        header_l.addWidget(self.title, 1)
        self.counter = QLabel()
        self.counter.setObjectName("Muted")
        header_l.addWidget(self.counter)
        root.addWidget(header)

        self.view = ZoomView()
        root.addWidget(self.view, 1)

        footer = QWidget()
        footer.setObjectName("ViewerFooter")
        footer_l = QHBoxLayout(footer)
        footer_l.setContentsMargins(16, 10, 16, 10)
        footer_l.setSpacing(8)

        self.prev_btn = QPushButton("‹")
        self.prev_btn.setObjectName("Ghost")
        self.prev_btn.setFixedWidth(40)
        self.prev_btn.clicked.connect(lambda: self.navigate(-1))
        footer_l.addWidget(self.prev_btn)

        self.next_btn = QPushButton("›")
        self.next_btn.setObjectName("Ghost")
        self.next_btn.setFixedWidth(40)
        self.next_btn.clicked.connect(lambda: self.navigate(1))
        footer_l.addWidget(self.next_btn)

        self.fit_btn = QPushButton("По размеру")
        self.fit_btn.setObjectName("Ghost")
        self.fit_btn.clicked.connect(self.view.fit_to_window)
        footer_l.addWidget(self.fit_btn)

        self.zoom_label = QLabel()
        self.zoom_label.setObjectName("Muted")
        footer_l.addWidget(self.zoom_label)
        self.view.zoomed.connect(lambda percent: self.zoom_label.setText(f"{percent}%"))

        footer_l.addStretch(1)

        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.accept)
        footer_l.addWidget(close_btn)
        root.addWidget(footer)

        for keys, slot in (
            (QKeySequence(Qt.Key.Key_Left), lambda: self.navigate(-1)),
            (QKeySequence(Qt.Key.Key_Right), lambda: self.navigate(1)),
            (QKeySequence(Qt.Key.Key_Escape), self.reject),
            (QKeySequence(Qt.Key.Key_F11), self._toggle_fullscreen),
            (QKeySequence(Qt.Key.Key_0), self.view.fit_to_window),
            (QKeySequence(Qt.Key.Key_1), self.view.actual_size),
        ):
            shortcut = QShortcut(keys, self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(slot)

        self._load()

    # -- navigation --------------------------------------------------------
    def navigate(self, delta: int) -> None:
        new_index = self._index + delta
        if 0 <= new_index < len(self._photos):
            self._index = new_index
            self._load()

    def _load(self) -> None:
        if not self._photos:
            self.title.setText("Нет фотографии")
            self.counter.setText("")
            self._set_placeholder("Нет фотографии")
            return

        photo = self._photos[self._index]
        self.title.setText(photo.file_name)
        self.title.setToolTip(photo.path)
        self.setWindowTitle(f"{photo.file_name} — Pazik Manager")
        self.counter.setText(f"{self._index + 1} / {len(self._photos)}")
        self.prev_btn.setEnabled(self._index > 0)
        self.next_btn.setEnabled(self._index < len(self._photos) - 1)

        ext = Path(photo.path).suffix.lower().lstrip(".")
        if ext and ext not in RASTER_EXTENSIONS:
            from core import exiftool

            if not exiftool.is_available():
                self._set_placeholder(
                    "Для RAW-файлов требуется ExifTool\n(См. Справка → ExifTool)"
                )
                return

        pixmap = self._load_pixmap(photo)
        if pixmap is None or pixmap.isNull():
            self._set_placeholder(
                "Файл недоступен"
                if photo.is_missing
                else "Не удалось загрузить изображение"
            )
            return

        self.view.set_pixmap(pixmap)
        self._update_zoom_label()
        # Keep the zoom label in sync after wheel zoom.
        self.view.setFocus()

    def _load_pixmap(self, photo: Photo) -> Optional[QPixmap]:
        path = Path(photo.path)
        if not path.is_file():
            return None

        ext = path.suffix.lower().lstrip(".")
        if ext in RASTER_EXTENSIONS:
            return self._read_raster(path)

        data = thumbnail.preview_bytes(path)
        if not data:
            return None
        pixmap = QPixmap()
        return pixmap if pixmap.loadFromData(data) else None

    @staticmethod
    def _read_raster(path: Path) -> Optional[QPixmap]:
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        size = reader.size()
        if size.isValid() and max(size.width(), size.height()) > _MAX_DECODE_EDGE:
            scaled = size.scaled(
                QSize(_MAX_DECODE_EDGE, _MAX_DECODE_EDGE),
                Qt.AspectRatioMode.KeepAspectRatio,
            )
            reader.setScaledSize(scaled)
        image = reader.read()
        if image.isNull():
            return None
        return QPixmap.fromImage(image)

    def _set_placeholder(self, text: str) -> None:
        self.view.set_pixmap(QPixmap())
        # A pixmap-less scene shows the background; overlay is not needed for
        # the MVP, but the title carries the reason.
        self.zoom_label.setText("")
        self.title.setText(text)
        self.counter.setText(f"{self._index + 1} / {len(self._photos)}")

    def _update_zoom_label(self) -> None:
        self.zoom_label.setText(f"{self.view.zoom_percent()}%")

    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    # -- events ------------------------------------------------------------
    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if event.key() in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.view.zoom_by(_ZOOM_STEP)
            self._update_zoom_label()
        elif event.key() == Qt.Key.Key_Minus:
            self.view.zoom_by(1 / _ZOOM_STEP)
            self._update_zoom_label()
        else:
            super().keyPressEvent(event)