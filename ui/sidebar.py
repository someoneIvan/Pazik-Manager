"""Left sidebar: libraries, quick sections and the tag vocabulary tree."""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QAction, QColor
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.catalog import Catalog

# Selection payload kinds understood by MainWindow.
KIND_SECTION = "section"
KIND_LIBRARY = "library"
KIND_TAG = "tag"


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
        self.setMinimumWidth(230)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 14, 12, 14)
        root.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("PAZIK")
        title.setObjectName("Heading")
        header.addWidget(title)
        header.addStretch(1)
        add_btn = QPushButton("+")
        add_btn.setObjectName("Ghost")
        add_btn.setFixedWidth(30)
        add_btn.setToolTip("Добавить папку в библиотеку")
        add_btn.clicked.connect(self.add_library_requested.emit)
        header.addWidget(add_btn)
        root.addLayout(header)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(1)
        self.tree.setIndentation(14)
        self.tree.setExpandsOnDoubleClick(True)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.itemSelectionChanged.connect(self._on_selection)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)
        root.addWidget(self.tree, 1)

        manage_btn = QPushButton("Менеджер тегов")
        manage_btn.setObjectName("Ghost")
        manage_btn.clicked.connect(self.manage_tags_requested.emit)
        root.addWidget(manage_btn)

        self.reload()

    # -- data --------------------------------------------------------------
    def reload(self, keep_selection: bool = False) -> None:
        prev = self._current_payload() if keep_selection else None
        self.tree.clear()

        # Quick sections
        for key, label in (
            ("all", "Все фотографии"),
            ("untagged", "Без тегов"),
            ("missing", "Отсутствующие"),
        ):
            item = QTreeWidgetItem(self.tree, [label])
            item.setData(0, Qt.ItemDataRole.UserRole, (KIND_SECTION, key))

        # Libraries
        libraries = self.catalog.list_libraries()
        if libraries:
            lib_root = self._group_item("Библиотеки")
            for lib in libraries:
                suffix = "" if lib.is_available else "  ·  нет доступа"
                item = QTreeWidgetItem(
                    lib_root, [f"{lib.name}  ·  {lib.photo_count}{suffix}"]
                )
                item.setData(0, Qt.ItemDataRole.UserRole, (KIND_LIBRARY, lib.id))
                item.setToolTip(0, lib.path)
                if not lib.is_available:
                    item.setForeground(0, QColor("#ff5c5c"))

        # Tag categories
        categories = self.catalog.list_categories()
        if categories:
            tag_root = self._group_item("Теги")
            for category in categories:
                tags = self.catalog.list_tags(category.id)
                if not tags and not category.is_builtin:
                    continue
                cat_item = self._group_item(category.name, tag_root)
                cat_item.setForeground(0, QColor(category.color))
                for tag in tags:
                    item = QTreeWidgetItem(
                        cat_item, [f"{tag.name}  ·  {tag.photo_count}"]
                    )
                    item.setData(0, Qt.ItemDataRole.UserRole, (KIND_TAG, tag.id))
                    item.setToolTip(0, f"{category.name} · {tag.photo_count} фото")

        self.tree.expandAll()
        if keep_selection and prev is not None:
            self._restore_selection(prev)

    def _group_item(self, label: str, parent=None) -> QTreeWidgetItem:
        item = QTreeWidgetItem(parent or self.tree, [label])
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
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