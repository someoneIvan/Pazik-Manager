"""Background workers that keep the UI responsive during long operations."""

from __future__ import annotations

import logging

from PyQt6.QtCore import QThread, pyqtSignal

from core.catalog import Catalog
from core.models import Library
from core.scanner import Scanner

log = logging.getLogger(__name__)


class ScanWorker(QThread):
    """Runs :class:`core.scanner.Scanner` on a library in the background.

    The worker opens its own :class:`Catalog` (and therefore its own SQLite
    connection), so it never shares a connection with the UI thread.  Calling
    :meth:`request_cancel` makes the scanner stop at the next file boundary.
    """

    progress = pyqtSignal(int, int, str)   # done, total, current path
    finished_scan = pyqtSignal(object)     # ScanResult
    failed = pyqtSignal(str)

    def __init__(
        self,
        db_path: str,
        library: Library,
        make_thumbnails: bool = True,
        regenerate_thumbnails: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self._db_path = db_path
        self._library = library
        self._make_thumbnails = make_thumbnails
        self._regenerate_thumbnails = regenerate_thumbnails
        self._cancel_requested = False

    def request_cancel(self) -> None:
        self._cancel_requested = True

    def _is_cancelled(self) -> bool:
        return self._cancel_requested

    def run(self) -> None:  # executed in the worker thread
        catalog = Catalog(self._db_path)
        try:
            scanner = Scanner(
                catalog,
                progress=lambda d, t, p: self.progress.emit(d, t, p),
                cancel_check=self._is_cancelled,
            )
            result = scanner.scan_library(
                self._library,
                make_thumbnails=self._make_thumbnails,
                regenerate_thumbnails=self._regenerate_thumbnails,
            )
            self.finished_scan.emit(result)
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("Scan failed")
            self.failed.emit(str(exc))
        finally:
            catalog.close()