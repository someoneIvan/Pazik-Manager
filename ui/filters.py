"""Right-hand filter panel (date range, camera, availability)."""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QDate, QSize, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import icons

#: Sentinel values for the date editors: showing the minimum date renders the
#: "special value text" (an em dash) instead of a real date.
_MIN_DATE = QDate(1900, 1, 1)
_DEFAULT_FROM = QDate(1990, 1, 1)


class FilterPanel(QWidget):
    """Collects filter criteria and emits :attr:`changed` when they change."""

    changed = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._loading = False

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(6)
        header_icon = QLabel()
        header_icon.setPixmap(icons.filter_icon().pixmap(QSize(14, 14)))
        header.addWidget(header_icon)
        title = QLabel("ФИЛЬТРЫ")
        title.setObjectName("SectionTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.reset_btn = QPushButton("Сбросить")
        self.reset_btn.setObjectName("Ghost")
        self.reset_btn.setToolTip("Сбросить все фильтры")
        self.reset_btn.clicked.connect(self.reset)
        header.addWidget(self.reset_btn)
        root.addLayout(header)

        # --- date range ---------------------------------------------------
        date_card, date_layout = self._card()
        date_layout.addLayout(self._group_label(icons.calendar_icon(), "Дата съёмки"))
        date_row = QHBoxLayout()
        date_row.setSpacing(6)

        self.date_from = self._make_date_edit()
        self.enable_from = QCheckBox("с")
        self.enable_from.toggled.connect(self._on_from_toggled)
        date_row.addWidget(self.enable_from)
        date_row.addWidget(self.date_from, 1)

        self.date_to = self._make_date_edit()
        self.enable_to = QCheckBox("по")
        self.enable_to.toggled.connect(self._on_to_toggled)
        date_row.addWidget(self.enable_to)
        date_row.addWidget(self.date_to, 1)
        date_layout.addLayout(date_row)
        root.addWidget(date_card)

        # --- camera -------------------------------------------------------
        camera_card, camera_layout = self._card()
        camera_layout.addLayout(self._group_label(icons.camera_icon(), "Камера"))
        self.camera = QComboBox()
        self.camera.addItem("Любая", None)
        self.camera.currentIndexChanged.connect(self._emit)
        camera_layout.addWidget(self.camera)
        root.addWidget(camera_card)

        # --- availability -------------------------------------------------
        state_card, state_layout = self._card()
        state_layout.addLayout(self._group_label(icons.check_circle_icon(), "Состояние"))
        self.only_missing = QCheckBox("Только отсутствующие файлы")
        self.only_missing.toggled.connect(self._emit)
        state_layout.addWidget(self.only_missing)

        self.only_untagged = QCheckBox("Только без тегов")
        self.only_untagged.toggled.connect(self._emit)
        state_layout.addWidget(self.only_untagged)
        root.addWidget(state_card)

        root.addStretch(1)

    # -- construction helpers ---------------------------------------------
    @staticmethod
    def _card() -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("FilterCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 9, 10, 10)
        layout.setSpacing(6)
        return card, layout

    @staticmethod
    def _group_label(icon, text: str) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)
        icon_label = QLabel()
        icon_label.setPixmap(icon.pixmap(QSize(14, 14)))
        row.addWidget(icon_label)
        label = QLabel(text)
        label.setObjectName("Muted")
        row.addWidget(label)
        row.addStretch(1)
        return row

    def _make_date_edit(self) -> QDateEdit:
        edit = QDateEdit()
        edit.setCalendarPopup(True)
        edit.setDisplayFormat("dd.MM.yyyy")
        edit.setDateRange(_MIN_DATE, QDate(2999, 12, 31))
        edit.setSpecialValueText("—")
        # Start at the minimum so the editor shows "—" while disabled.
        edit.setDate(_MIN_DATE)
        edit.setEnabled(False)
        edit.dateChanged.connect(self._emit)
        return edit

    # -- public API --------------------------------------------------------
    def set_camera_models(self, models: list[str]) -> None:
        self._loading = True
        current = self.camera.currentData()
        self.camera.clear()
        self.camera.addItem("Любая", None)
        for model in models:
            self.camera.addItem(model, model)
        idx = self.camera.findData(current)
        self.camera.setCurrentIndex(idx if idx >= 0 else 0)
        self._loading = False

    def values(self) -> dict:
        return {
            "date_from": self.date_from.date().toString("yyyy-MM-dd")
            if self.enable_from.isChecked()
            else None,
            "date_to": self.date_to.date().toString("yyyy-MM-dd")
            if self.enable_to.isChecked()
            else None,
            "camera_model": self.camera.currentData(),
            "missing": True if self.only_missing.isChecked() else None,
            "untagged": self.only_untagged.isChecked(),
        }

    def reset(self) -> None:
        self._loading = True
        self.enable_from.setChecked(False)
        self.enable_to.setChecked(False)
        self.date_from.setDate(_MIN_DATE)
        self.date_to.setDate(_MIN_DATE)
        self.camera.setCurrentIndex(0)
        self.only_missing.setChecked(False)
        self.only_untagged.setChecked(False)
        self._loading = False
        self._emit()

    # -- internals ---------------------------------------------------------
    def _on_from_toggled(self, checked: bool) -> None:
        self.date_from.setEnabled(checked)
        if checked and self.date_from.date() == _MIN_DATE:
            self._loading = True
            self.date_from.setDate(_DEFAULT_FROM)
            self._loading = False
        self._clamp_dates()
        self._emit()

    def _on_to_toggled(self, checked: bool) -> None:
        self.date_to.setEnabled(checked)
        if checked and self.date_to.date() == _MIN_DATE:
            self._loading = True
            self.date_to.setDate(QDate.currentDate())
            self._loading = False
        self._clamp_dates()
        self._emit()

    def _clamp_dates(self) -> None:
        if self.enable_from.isChecked() and self.enable_to.isChecked():
            if self.date_from.date() > self.date_to.date():
                self._loading = True
                self.date_to.setDate(self.date_from.date())
                self._loading = False

    def _emit(self) -> None:
        if not self._loading:
            self.changed.emit()