"""Left sidebar: libraries, quick sections and the tag vocabulary tree."""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.catalog import Catalog

from . import icons
from .theme import DANGER, MUTED

# Selection payload kinds understood by MainWindow.
KIND_SECTION = "section"
KIND_LIBRARY = "library"
KIND_TAG = "tag"

_SELECTION = QColor(79, 140, 255, 72)
_HOVER = QColor(255, 255, 255, 14)
_CHEVRON = QColor(MUTED)
_COUNT = QColor(MUTED)


class _SidebarItemDelegate(QStyledItemDelegate):
    """Paints one rounded, full-width highlight per row.

    Per-cell QSS backgrounds leave a seam between the name and count columns;
    painting the highlight here keeps it as a single rounded pill.
    """

    def __init__(self, tree: QTreeWidget):
        super().__init__(tree)
        self._tree = tree

    def paint(self, painter, option, index) -> None:
        if index.column() == 0:
            rect = QRectF(
                0.0,
                float(option.rect.y()),
                float(self._tree.viewport().width()),
                float(option.rect.height()),
            )
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            if option.state & QStyle.StateFlag.State_Selected:
                painter.setBrush(_SELECTION)
                painter.drawRoundedRect(rect, 8.0, 8.0)
            elif option.state & QStyle.StateFlag.State_MouseOver:
                painter.setBrush(_HOVER)
                painter.drawRoundedRect(rect, 8.0, 8.0)
            painter.restore()

        super().paint(painter, option, index)


class _SidebarTree(QTreeWidget):
    """Tree with clean chevron indicators instead of boxed default arrows."""

    def drawBranches(self, painter, rect, index) -> None:
        if not self.model().hasChildren(index):
            return

        depth = 0
        parent = index.parent()
        while parent.isValid():
            depth += 1
            parent = parent.parent()

        indent = self.indentation()
        cx = depth * indent + indent / 2.0
        cy = rect.center().y()
        expanded = self.isExpanded(index)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(_CHEVRON, 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)

        path = QPainterPath()
        if expanded:
            path.moveTo(cx - 3.5, cy - 1.8)
            path.lineTo(cx, cy + 1.8)
            path.lineTo(cx + 3.5, cy - 1.8)
        else:
            path.moveTo(cx - 1.8, cy - 3.5)
            path.lineTo(cx + 1.8, cy)
            path.lineTo(cx - 1.8, cy + 3.5)
        painter.drawPath(path)
        painter.restore()


class Sidebar(QWidget):
    """Navigation tree emitting high-level selection events."""

    library_selected = pyqtSignal(int)
    tag_selected = pyqtSignal(int)
    section_selected = pyqtSignal(str)   # "all" | "untagged" | "missing"

    add_library_requested = pyqtSignal()
    manage_tags_requested = pyqtSignal()
    rescan_library_requested = pyqtSignal(int)
    open_library_requested = pyqtSignal(int)
    relocate_library_requested = pyqtSignal(int)
    remove_library_requested = pyqtSignal(int)

    def __init__(self, catalog: Catalog, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.catalog = catalog
        self.setObjectName("Sidebar")
        self.setMinimumWidth(250)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 14, 12, 14)
        root.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(6)
        title = QLabel("PAZIK")
        title.setObjectName("Heading")
        header.addWidget(title)
        header.addStretch(1)
        self.add_library_btn = QPushButton()
        self.add_library_btn.setObjectName("IconButton")
        self.add_library_btn.setIcon(icons.folder_plus_icon())
        self.add_library_btn.setIconSize(QSize(18, 18))
        self.add_library_btn.setFixedSize(32, 32)
        self.add_library_btn.setToolTip("Добавить папку в библиотеку")
        self.add_library_btn.setAccessibleName("Добавить папку в библиотеку")
        self.add_library_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_library_btn.clicked.connect(self.add_library_requested.emit)
        header.addWidget(self.add_library_btn)
        root.addLayout(header)

        self.tree = _SidebarTree()
        self.tree.setObjectName("SidebarTree")
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(2)
        self.tree.setIndentation(14)
        self.tree.setIconSize(QSize(16, 16))
        self.tree.setUniformRowHeights(True)
        self.tree.setExpandsOnDoubleClick(True)
        self.tree.setMouseTracking(True)
        self.tree.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        tree_header = self.tree.header()
        tree_header.setStretchLastSection(False)
        tree_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        tree_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setItemDelegate(_SidebarItemDelegate(self.tree))
        self.tree.itemSelectionChanged.connect(self._on_selection)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)
        root.addWidget(self.tree, 1)

        self.manage_btn = QPushButton("Менеджер тегов")
        self.manage_btn.setObjectName("Ghost")
        self.manage_btn.setIcon(icons.tag_icon())
        self.manage_btn.setIconSize(QSize(16, 16))
        self.manage_btn.setMinimumHeight(34)
        self.manage_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.manage_btn.clicked.connect(self.manage_tags_requested.emit)
        root.addWidget(self.manage_btn)

        self.reload()

    # -- data --------------------------------------------------------------
    def reload(self, keep_selection: bool = False) -> None:
        prev = self._current_payload() if keep_selection else None
        self.tree.clear()

        # Quick sections
        for key, label, icon, tip in (
            ("all", "Все фотографии", icons.photos_icon(), "Все фотографии в каталоге"),
            ("untagged", "Без тегов", icons.untagged_icon(), "Фотографии без тегов"),
            ("missing", "Отсутствующие", icons.missing_icon(), "Файлы, которых нет на диске"),
        ):
            self._item(self.tree, label, icon=icon,
                       payload=(KIND_SECTION, key), tooltip=tip)

        # Libraries
        libraries = self.catalog.list_libraries()
        if libraries:
            lib_root = self._section_item("Библиотеки")
            for lib in libraries:
                available = lib.is_available
                item = self._item(
                    lib_root,
                    lib.name,
                    icon=icons.folder_icon(MUTED if available else DANGER),
                    payload=(KIND_LIBRARY, lib.id),
                    count=lib.photo_count,
                    tooltip=(
                        f"{lib.path}\n{lib.photo_count} фото"
                        if available
                        else f"Папка недоступна:\n{lib.path}"
                    ),
                )
                if not available:
                    item.setForeground(0, QColor(DANGER))

        # Tag categories
        categories = self.catalog.list_categories()
        if categories:
            tag_root = self._section_item("Теги")
            for category in categories:
                tags = self.catalog.list_tags(category.id)
                if not tags and not category.is_builtin:
                    continue
                cat_item = self._category_item(category.name, category.color, tag_root)
                for tag in tags:
                    self._item(
                        cat_item,
                        tag.name,
                        icon=icons.dot_icon(category.color),
                        payload=(KIND_TAG, tag.id),
                        count=tag.photo_count,
                        tooltip=f"{category.name} · {tag.photo_count} фото",
                    )

        self.tree.expandAll()
        self.tree.resizeColumnToContents(1)
        if keep_selection and prev is not None:
            self._restore_selection(prev)

    def _item(
        self,
        parent,
        label: str,
        *,
        icon=None,
        payload=None,
        count: Optional[int | str] = None,
        tooltip: Optional[str] = None,
    ) -> QTreeWidgetItem:
        item = QTreeWidgetItem(parent, [label, "" if count is None else str(count)])
        if icon is not None and not icon.isNull():
            item.setIcon(0, icon)
        if payload is not None:
            item.setData(0, Qt.ItemDataRole.UserRole, payload)
        if count is not None:
            item.setTextAlignment(
                1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            item.setForeground(1, _COUNT)
        if tooltip:
            item.setToolTip(0, tooltip)
            item.setToolTip(1, tooltip)
        return item

    def _section_item(self, label: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem(self.tree, [label, ""])
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        font = QFont(self.font())
        font.setPointSizeF(8.5)
        font.setWeight(QFont.Weight.DemiBold)
        font.setCapitalization(QFont.Capitalization.AllUppercase)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.0)
        item.setFont(0, font)
        item.setForeground(0, QColor(MUTED))
        return item

    def _category_item(self, label: str, color: str, parent) -> QTreeWidgetItem:
        item = QTreeWidgetItem(parent, [label, ""])
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        font = QFont(self.font())
        font.setWeight(QFont.Weight.Medium)
        item.setFont(0, font)
        item.setForeground(0, QColor(color))
        item.setToolTip(0, label)
        return item

    # -- selection ---------------------------------------------------------
    def _on_selection(self) -> None:
        payload = self._current_payload()
        if not payload:
            return
        kind, value = payload
        if kind == KIND_LIBRARY:
            self.library_selected.emit(int(value))
        elif kind == KIND_TAG:
            self.tag_selected.emit(int(value))
        elif kind == KIND_SECTION:
            self.section_selected.emit(str(value))

    def _current_payload(self):
        item = self.tree.currentItem()
        if item is None:
            return None
        return item.data(0, Qt.ItemDataRole.UserRole)

    def _restore_selection(self, payload) -> None:
        kind, value = payload
        for item in _iter_items(self.tree):
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data and data[0] == kind and str(data[1]) == str(value):
                self.tree.setCurrentItem(item)
                return

    def select_section(self, key: str) -> None:
        self._restore_selection((KIND_SECTION, key))

    # -- context menu ------------------------------------------------------
    def _on_context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data or data[0] != KIND_LIBRARY:
            return
        library_id = int(data[1])

        menu = QMenu(self)
        rescan = QAction("Пересканировать", menu)
        rescan.triggered.connect(lambda: self.rescan_library_requested.emit(library_id))
        open_dir = QAction("Открыть папку", menu)
        open_dir.triggered.connect(lambda: self.open_library_requested.emit(library_id))
        relocate = QAction("Указать новую папку…", menu)
        relocate.triggered.connect(
            lambda: self.relocate_library_requested.emit(library_id)
        )
        remove = QAction("Убрать из библиотеки", menu)
        remove.triggered.connect(lambda: self.remove_library_requested.emit(library_id))
        menu.addAction(rescan)
        menu.addAction(open_dir)
        menu.addAction(relocate)
        menu.addSeparator()
        menu.addAction(remove)
        menu.exec(self.tree.mapToGlobal(pos))


def _iter_items(tree: QTreeWidget):
    stack = [tree.invisibleRootItem().child(i) for i in range(tree.topLevelItemCount())]
    while stack:
        item = stack.pop()
        yield item
        for i in range(item.childCount()):
            stack.append(item.child(i))