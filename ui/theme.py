"""Theme / asset loading helpers."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QColor, QIcon

_RESOURCE_ROOT = Path(__file__).resolve().parent.parent / "resources"
_STYLE_PATH = _RESOURCE_ROOT / "styles" / "dark.qss"
_ICON_PATH = _RESOURCE_ROOT / "icons" / "app.png"

# Palette exposed to widgets that need a colour without hard-coding it.
ACCENT = "#4f8cff"
TEXT = "#e6e8ec"
MUTED = "#8a8f98"
DANGER = "#ff5c5c"


def load_stylesheet() -> str:
    """Return the application QSS, or an empty string if it is missing."""
    try:
        return _STYLE_PATH.read_text(encoding="utf-8")
    except OSError:
        return ""


def app_icon() -> QIcon:
    """Return the application icon (empty if no icon file is bundled)."""
    if _ICON_PATH.is_file():
        return QIcon(str(_ICON_PATH))
    return QIcon()


def accent_color() -> QColor:
    return QColor(ACCENT)


class IconProvider(QObject):
    """Loads a small set of bundled icons, synthesising a fallback if absent.

    Icons are optional; when no icon files are shipped the provider returns an
    empty :class:`QIcon` and callers fall back to text, keeping the UI usable.
    """

    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dir = _RESOURCE_ROOT / "icons"
        self._cache: dict[str, QIcon] = {}

    def icon(self, name: str) -> QIcon:
        if name in self._cache:
            return self._cache[name]
        path = self._dir / name
        icon = QIcon(str(path)) if path.is_file() else QIcon()
        self._cache[name] = icon
        return icon