"""Geotag editor dialog."""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.geo import is_valid, parse_coordinates

from . import icons


class GeoTagDialog(QDialog):
    """Enter coordinates directly or paste a link to a map."""

    def __init__(
        self,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._result: Optional[tuple[float, float]] = None
        self.setWindowTitle("Геометка")
        self.setModal(True)
        self.setMinimumWidth(420)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(10)
        pin = QLabel()
        pin.setPixmap(icons.pin_icon().pixmap(QSize(22, 22)))
        header.addWidget(pin, 0, Qt.AlignmentFlag.AlignTop)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        title = QLabel("Координаты фотографии")
        title.setObjectName("DialogTitle")
        titles.addWidget(title)
        hint = QLabel(
            "Вставьте ссылку на карту или введите координаты «55.7558, 37.6173»."
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        titles.addWidget(hint)
        header.addLayout(titles, 1)
        root.addLayout(header)

        root.addWidget(self._caption("Ссылка или координаты"))
        self.paste_edit = QLineEdit()
        self.paste_edit.setPlaceholderText("https://…  или  55.7558, 37.6173")
        self.paste_edit.textChanged.connect(self._on_paste_changed)
        root.addWidget(self.paste_edit)

        coords = QHBoxLayout()
        coords.setSpacing(10)
        coords.addLayout(self._coordinate_field("Широта", latitude))
        coords.addLayout(self._coordinate_field("Долгота", longitude))
        root.addLayout(coords)

        self.error_label = QLabel("")
        self.error_label.setObjectName("ErrorText")
        self.error_label.setWordWrap(True)
        root.addWidget(self.error_label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Отмена")
        cancel.setObjectName("Ghost")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        save = QPushButton("Сохранить")
        save.setObjectName("Primary")
        save.setDefault(True)
        save.clicked.connect(self._on_save)
        buttons.addWidget(save)
        root.addLayout(buttons)

        if latitude is not None and longitude is not None:
            self.lat_edit.setText(f"{latitude:.6f}")
            self.lon_edit.setText(f"{longitude:.6f}")
        self.lat_edit.setFocus()

    # -- results -----------------------------------------------------------
    def coordinates(self) -> Optional[tuple[float, float]]:
        return self._result

    # -- internals ---------------------------------------------------------
    def _coordinate_field(self, title: str, value: Optional[float]) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setSpacing(4)
        column.addWidget(self._caption(title))
        edit = QLineEdit()
        edit.setPlaceholderText("55.755800" if title == "Широта" else "37.617300")
        if value is not None:
            edit.setText(f"{value:.6f}")
        column.addWidget(edit)
        if title == "Широта":
            self.lat_edit = edit
        else:
            self.lon_edit = edit
        return column

    @staticmethod
    def _caption(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("Muted")
        return label

    def _on_paste_changed(self, text: str) -> None:
        if not text.strip():
            return
        parsed = parse_coordinates(text)
        if parsed is None:
            return
        self.lat_edit.setText(f"{parsed[0]:.6f}")
        self.lon_edit.setText(f"{parsed[1]:.6f}")
        self._set_error("")

    def _set_error(self, message: str) -> None:
        self.error_label.setText(message)

    def _read_coordinate(self, edit: QLineEdit, label: str) -> Optional[float]:
        text = edit.text().strip().replace(",", ".")
        if not text:
            self._set_error(f"Укажите {label.lower()}.")
            edit.setFocus()
            return None
        try:
            return float(text)
        except ValueError:
            self._set_error(f"{label}: введите число.")
            edit.setFocus()
            return None

    def _on_save(self) -> None:
        latitude = self._read_coordinate(self.lat_edit, "Широта")
        if latitude is None:
            return
        longitude = self._read_coordinate(self.lon_edit, "Долгота")
        if longitude is None:
            return
        if not is_valid(latitude, longitude):
            self._set_error(
                "Проверьте диапазоны: широта от −90 до 90, долгота от −180 до 180."
            )
            return
        self._result = (latitude, longitude)
        self.accept()