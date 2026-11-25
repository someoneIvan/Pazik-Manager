"""Pazik Manager — entry point.

Usage:
    python main.py              # launch in full-screen mode
    python main.py --windowed   # launch in a normal resizable window

Run ``python -m pytest`` to execute the test-suite.
"""

from __future__ import annotations

import logging
import sys
import traceback
from pathlib import Path

# Ensure the project root is importable when launched as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from core import exiftool  # noqa: E402
from core.catalog import Catalog  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402
from ui.theme import app_icon, load_stylesheet, ui_font  # noqa: E402

log = logging.getLogger(__name__)


def _install_exception_hook() -> None:
    """Log uncaught exceptions and show a dialog instead of dying silently."""

    def hook(exc_type, exc, tb):
        log.error(
            "Unhandled exception:\n%s",
            "".join(traceback.format_exception(exc_type, exc, tb)),
        )
        try:
            QMessageBox.critical(
                None,
                "Pazik Manager — ошибка",
                f"Произошла непредвиденная ошибка:\n\n{exc_type.__name__}: {exc}",
            )
        except Exception:  # pragma: no cover - never fail inside the hook
            pass

    sys.excepthook = hook


def _configure_logging() -> None:
    """Console + rotating file log under the application data directory."""
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        from logging.handlers import RotatingFileHandler

        from core import paths

        log_file = paths.logs_dir() / "pazik.log"
        handlers.append(
            RotatingFileHandler(
                log_file, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
            )
        )
    except Exception:  # pragma: no cover - logging must never break startup
        pass

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    windowed = "--windowed" in argv
    argv = [arg for arg in argv if arg != "--windowed"]

    _configure_logging()

    app = QApplication(argv)
    app.setApplicationName("Pazik Manager")
    app.setApplicationDisplayName("Pazik Manager")
    app.setOrganizationName("Pazik")
    app.setStyle("Fusion")

    # Moscow Sans (or the best available fallback) for the whole interface.
    app.setFont(ui_font())

    icon = app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)

    qss = load_stylesheet()
    if qss:
        app.setStyleSheet(qss)

    if not exiftool.is_available():
        # Informational only — JPEG/PNG/TIFF work without it, and the UI has a
        # dedicated "Справка → ExifTool" dialog with install instructions.
        log.info(
            "ExifTool not found; RAW support is off. Install it (e.g. "
            "`winget install OliverBetz.ExifTool`) or place exiftool.exe in "
            "resources/tools/."
        )
    else:
        log.info("ExifTool found: %s", exiftool.get_exiftool_path())

    _install_exception_hook()

    catalog = Catalog()

    # One-time repair for duplicates left behind by older versions where moving
    # a file inside a library created a fresh row and orphaned the tagged one.
    # Idempotent: a no-op once the catalogue is clean.
    try:
        merged = catalog.merge_duplicate_photos()
        if merged:
            log.info("Merged %s duplicate photo row(s) at startup", merged)
    except Exception:  # pragma: no cover - repair must never block startup
        log.exception("Duplicate-photo cleanup failed")

    window = MainWindow(catalog)
    if not icon.isNull():
        window.setWindowIcon(icon)

    # Launch full-screen by default; --windowed is handy for development.
    if windowed:
        window.show()
    else:
        window.showFullScreen()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())