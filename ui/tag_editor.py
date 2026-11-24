"""Dialogs for assigning tags to a photo and for managing the tag vocabulary."""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.catalog import Catalog
from core.models import Photo


class TagAssignDialog(QDialog):
    """Pick tags (grouped by category) to apply to a single photo."""

    def __init__(self, catalog: Catalog, photo: Photo, parent=None):
        super().__init__(parent)
        self.catalog = catalog
        self.photo = photo
        self.setWindowTitle("Назначить теги")
        self.resize(440, 560)

        self._checked: set[int] = {t.id for t in catalog.get_photo_tags(photo.id)}
        self._checks: dict[int, QCheckBox] = {}
        self._entries: list[tuple[QGroupBox, list[tuple[QCheckBox, str]]]] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        root.addWidget(QLabel(f"Теги для «{photo.file_name}»"))

        self.search = QLineEdit()
        self.search.setPlaceholderText("Фильтр тегов…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        root.addWidget(self.search)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(8)
        self.scroll.setWidget(self._content)
        root.addWidget(self.scroll, 1)

        new_tag_row = QHBoxLayout()
        new_tag_row.addStretch(1)
        self.new_tag_btn = QPushButton("Новый тег…")
        self.new_tag_btn.setObjectName("Ghost")
        self.new_tag_btn.clicked.connect(self._on_new_tag)
        new_tag_row.addWidget(self.new_tag_btn)
        root.addLayout(new_tag_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._rebuild()

    # -- content -----------------------------------------------------------
    def _rebuild(self, preserve: Optional[set[int]] = None) -> None:
        # Keep exactly the checkbox state the user currently sees.
        if preserve is None:
            preserve = self._selected_ids() if self._checks else set(self._checked)
        self._checked = set(preserve)
        self._checks.clear()
        self._entries.clear()

        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        any_tag = False
        for category in self.catalog.list_categories():
            tags = self.catalog.list_tags(category.id)
            if not tags:
                continue
            any_tag = True
            box = QGroupBox(category.name)
            inner = QVBoxLayout(box)
            inner.setSpacing(3)
            entries: list[tuple[QCheckBox, str]] = []
            for tag in tags:
                cb = QCheckBox(tag.name)
                cb.setChecked(tag.id in self._checked)
                self._checks[tag.id] = cb
                inner.addWidget(cb)
                entries.append((cb, tag.name.lower()))
            self._entries.append((box, entries))
            self._content_layout.addWidget(box)

        if not any_tag:
            hint = QLabel("Теги ещё не созданы. Нажмите «Новый тег…».")
            hint.setObjectName("Muted")
            self._content_layout.addWidget(hint)

        self._content_layout.addStretch(1)
        self._apply_filter(self.search.text())

    def _selected_ids(self) -> set[int]:
        return {tid for tid, cb in self._checks.items() if cb.isChecked()}

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for box, entries in self._entries:
            visible = 0
            for cb, name in entries:
                match = not needle or needle in name
                cb.setVisible(match)
                if match:
                    visible += 1
            box.setVisible(visible > 0)

    # -- actions -----------------------------------------------------------
    def _on_new_tag(self) -> None:
        categories = self.catalog.list_categories()
        if not categories:
            QMessageBox.information(self, "Теги", "Сначала создайте категорию.")
            return
        names = [c.name for c in categories]
        category_name, ok = QInputDialog.getItem(
            self, "Новый тег", "Категория:", names, 0, False
        )
        if not ok:
            return
        category = next(c for c in categories if c.name == category_name)

        name, ok = QInputDialog.getText(self, "Новый тег", "Название тега:")
        if not ok or not name.strip():
            return

        tag = self.catalog.create_tag(name.strip(), category.id)
        self._rebuild(preserve=self._selected_ids() | {tag.id})

    def accept(self) -> None:
        self.catalog.set_photo_tags(self.photo.id, self._selected_ids())
        super().accept()


class TagManagerDialog(QDialog):
    """Create / rename / delete categories and tags."""

    def __init__(self, catalog: Catalog, parent=None):
        super().__init__(parent)
        self.catalog = catalog
        self.setWindowTitle("Менеджер тегов")
        self.resize(500, 600)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        hint = QLabel(
            "Категории группируют теги. Двойной клик — переименовать."
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Категории и теги", "Фото"])
        self.tree.setColumnWidth(0, 360)
        self.tree.itemDoubleClicked.connect(lambda *_: self._rename())
        root.addWidget(self.tree, 1)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_add_cat = QPushButton("+ Категория")
        self.btn_add_tag = QPushButton("+ Тег")
        self.btn_rename = QPushButton("Переименовать")
        self.btn_delete = QPushButton("Удалить")
        for button in (
            self.btn_add_cat,
            self.btn_add_tag,
            self.btn_rename,
            self.btn_delete,
        ):
            row.addWidget(button)
        root.addLayout(row)

        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        root.addWidget(close)

        self.btn_add_cat.clicked.connect(self._add_category)
        self.btn_add_tag.clicked.connect(self._add_tag)
        self.btn_rename.clicked.connect(self._rename)
        self.btn_delete.clicked.connect(self._delete)

        self.reload()

    # -- data --------------------------------------------------------------
    def reload(self) -> None:
        selected = self._selected()
        self.tree.clear()
        categories = self.catalog.list_categories()
        for category in categories:
            cat_item = QTreeWidgetItem(self.tree, [category.name, ""])
            cat_item.setData(0, Qt.ItemDataRole.UserRole, ("category", category.id))
            cat_item.setForeground(0, QBrush(QColor(category.color)))
            font = cat_item.font(0)
            font.setBold(True)
            cat_item.setFont(0, font)
            for tag in self.catalog.list_tags(category.id):
                tag_item = QTreeWidgetItem(
                    cat_item, [tag.name, str(tag.photo_count)]
                )
                tag_item.setData(0, Qt.ItemDataRole.UserRole, ("tag", tag.id))
        self.tree.expandAll()
        if selected:
            self._select(selected)

    def _selected(self):
        item = self.tree.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item else None

    def _select(self, payload) -> None:
        kind, obj_id = payload
        for i in range(self.tree.topLevelItemCount()):
            cat_item = self.tree.topLevelItem(i)
            data = cat_item.data(0, Qt.ItemDataRole.UserRole)
            if data == payload:
                self.tree.setCurrentItem(cat_item)
                return
            for j in range(cat_item.childCount()):
                child = cat_item.child(j)
                if child.data(0, Qt.ItemDataRole.UserRole) == payload:
                    self.tree.setCurrentItem(child)
                    return

    # -- actions -----------------------------------------------------------
    def _add_category(self) -> None:
        name, ok = QInputDialog.getText(self, "Новая категория", "Название:")
        if ok and name.strip():
            self.catalog.create_category(name.strip())
            self.reload()

    def _add_tag(self) -> None:
        sel = self._selected()
        category_id: Optional[int] = None
        if sel and sel[0] == "category":
            category_id = sel[1]
        elif sel and sel[0] == "tag":
            category_id = self.catalog.get_tag(sel[1]).category_id

        categories = self.catalog.list_categories()
        if category_id is None:
            if not categories:
                QMessageBox.information(self, "Теги", "Сначала создайте категорию.")
                return
            names = [c.name for c in categories]
            category_name, ok = QInputDialog.getItem(
                self, "Новый тег", "Категория:", names, 0, False
            )
            if not ok:
                return
            category_id = next(c.id for c in categories if c.name == category_name)

        name, ok = QInputDialog.getText(self, "Новый тег", "Название:")
        if ok and name.strip():
            tag = self.catalog.create_tag(name.strip(), category_id)
            self.reload()
            self._select(("tag", tag.id))

    def _rename(self) -> None:
        sel = self._selected()
        if not sel:
            return
        kind, obj_id = sel
        current = self._name_of(kind, obj_id)
        name, ok = QInputDialog.getText(
            self, "Переименовать", "Название:", text=current
        )
        if not ok or not name.strip() or name.strip() == current:
            return
        if kind == "tag":
            self.catalog.rename_tag(obj_id, name.strip())
        else:
            self.catalog.rename_category(obj_id, name.strip())
        self.reload()

    def _delete(self) -> None:
        sel = self._selected()
        if not sel:
            return
        kind, obj_id = sel
        name = self._name_of(kind, obj_id)
        extra = (
            "\nВсе теги этой категории также будут удалены."
            if kind == "category"
            else "\nТег будет снят со всех фотографий."
        )
        confirm = QMessageBox.question(
            self,
            "Удалить",
            f"Удалить «{name}»?{extra}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        if kind == "tag":
            self.catalog.delete_tag(obj_id)
        else:
            self.catalog.delete_category(obj_id)
        self.reload()

    # -- helpers -----------------------------------------------------------
    def _name_of(self, kind: str, obj_id: int) -> str:
        if kind == "tag":
            return self.catalog.get_tag(obj_id).name
        for category in self.catalog.list_categories():
            if category.id == obj_id:
                return category.name
        return "?"