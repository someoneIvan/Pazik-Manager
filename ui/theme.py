"""Theme / asset loading helpers."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QIcon

_RESOURCE_ROOT = Path(__file__).resolve().parent.parent / "resources"
_STYLE_PATH = _RESOURCE_ROOT / "styles" / "dark.qss"
_ICON_PATH = _RESOURCE_ROOT / "icons" / "app.png"
_FONT_DIR = _RESOURCE_ROOT / "fonts"

# Preferred UI typeface, with fallbacks for machines where it is not installed.
# "Moscow Sans" is used when available (bundled in resources/fonts or installed
# system-wide); otherwise the first available fallback is used.
FONT_CANDIDATES = (
    "Moscow Sans",
    "MoscowSans",
    "Moscow Sans UI",
    "Segoe UI",
    "Inter",
    "Helvetica Neue",
    "Arial",
    "DejaVu Sans",
    "sans-serif",
)

# Palette exposed to widgets that need a colour without hard-coding it.
ACCENT = "#4f8cff"
TEXT = "#e6e8ec"
MUTED = "#8a8f98"
DANGER = "#ff5c5c"


def _load_bundled_fonts() -> None:
    """Register any .ttf/.otf files shipped in resources/fonts."""
    if not _FONT_DIR.is_dir():
        return
    for font_file in sorted(_FONT_DIR.iterdir()):
        if font_file.suffix.lower() in (".ttf", ".otf"):
            QFontDatabase.addApplicationFont(str(font_file))


def ui_font() -> QFont:
    """Return the application font, resolving the first available candidate."""
    _load_bundled_fonts()
    available = set(QFontDatabase.families())
    for name in FONT_CANDIDATES:
        if name in available:
            return QFont(name)
    return QFont()


def font_family() -> str:
    """Return the resolved font family name (for QSS)."""
    return ui_font().family()


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