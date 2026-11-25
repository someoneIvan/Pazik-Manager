"""Main application window.

Layout (per the specification):

    ┌──────────┬───────────────────────────────┬──────────────┐
    │ sidebar  │  search bar (top)             │  filters     │
    │ (libs,   │  gallery of thumbnails        │  photo info  │
    │  tags)   │                               │              │
    └──────────┴───────────────────────────────┴──────────────┘

The window owns the main-thread :class:`Catalog`; scans run on a
:class:`~ui.workers.ScanWorker` thread with its own connection.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QSettings, Qt, QTimer, QUrl
from PyQt6.QtGui import QAction, QDesktopServices, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSlider,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from core import thumbnail
from core.catalog import Catalog, PhotoQuery
from core.models import Photo

from .filters import FilterPanel
from .gallery import ACTION_COPY_PATH, ACTION_EXPLORER, ACTION_OPEN, Gallery
from .photo_info import PhotoInfo
from .sidebar import Sidebar
from .tag_editor import TagManagerDialog
from .theme import IconProvider, load_stylesheet
from .viewer import ImageViewer
from .workers import ScanWorker

log = logging.getLogger(__name__)

_ZOOM_DEFAULT = 200


class MainWindow(QMainWindow):
    def __init__(self, catalog: Optional[Catalog] = None):
        super().__init__()
        self.catalog = catalog or Catalog()
        self.icons = IconProvider(self)
        self.settings = QSettings("Pazik", "PazikManager")

        self.setWindowTitle("Pazik Manager")
        self.resize(1360, 860)
        self.setObjectName("RootView")

        # Filter/selection state
        self._library_id: Optional[int] = None
        self._tag_id: Optional[int] = None
        self._section: str = "all"
        self._search_text: str = ""
        self._photos: list[Photo] = []
        self._photos_by_id: dict[int, Photo] = {}

        self._scan_queue: list[tuple[int, bool]] = []
        self._active_worker: Optional[ScanWorker] = None
        self._closing = False

        # Debounce query refreshes triggered by typing / filter changes.
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(180)
        self._refresh_timer.timeout.connect(self._refresh_gallery)

        self._build_ui()
        self._build_menu()
        self._build_shortcuts()
        self._apply_theme()

        # Initial data load
        self.sidebar.reload()
        self._reload_camera_models()
        self.sidebar.select_section("all")
        self._refresh_gallery()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        self.sidebar = Sidebar(self.catalog)
        self.sidebar.library_selected.connect(self._on_library_selected)
        self.sidebar.tag_selected.connect(self._on_tag_selected)
        self.sidebar.section_selected.connect(self._on_section_selected)
        self.sidebar.add_library_requested.connect(self.add_library)
        self.sidebar.manage_tags_requested.connect(self.open_tag_manager)
        self.sidebar.rescan_library_requested.connect(self.rescan_library)
        self.sidebar.open_library_requested.connect(self.open_library)
        self.sidebar.relocate_library_requested.connect(self.relocate_library)
        self.sidebar.remove_library_requested.connect(self.remove_library)

        # --- top bar ------------------------------------------------------
        top = QWidget()
        top.setObjectName("TopBar")
        top_h = QHBoxLayout(top)
        top_h.setContentsMargins(14, 10, 14, 10)
        top_h.setSpacing(10)

        self.search = QLineEdit()
        self.search.setObjectName("SearchField")
        self.search.setPlaceholderText("Поиск по имени, тегу, камере…  (Ctrl+F)")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._on_search_changed)
        top_h.addWidget(self.search, 1)

        zoom_label = QLabel("Масштаб")
        zoom_label.setObjectName("Muted")
        top_h.addWidget(zoom_label)

        self.zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.zoom_slider.setRange(120, 340)
        self.zoom_slider.setSingleStep(10)
        self.zoom_slider.setPageStep(40)
        self.zoom_slider.setFixedWidth(130)
        self.zoom_slider.setToolTip("Размер миниатюр")
        try:
            saved_zoom = int(self.settings.value("gallery/icon_size", _ZOOM_DEFAULT))
        except (TypeError, ValueError):
            saved_zoom = _ZOOM_DEFAULT
        saved_zoom = max(120, min(340, saved_zoom))
        self.zoom_slider.setValue(saved_zoom)
        self.zoom_slider.valueChanged.connect(self._on_zoom_changed)
        top_h.addWidget(self.zoom_slider)

        self.rescan_btn = QPushButton("Обновить")
        self.rescan_btn.setObjectName("Ghost")
        self.rescan_btn.setToolTip("Пересканировать библиотеки (F5)")
        self.rescan_btn.clicked.connect(self._on_rescan_clicked)
        top_h.addWidget(self.rescan_btn)

        self.add_btn = QPushButton("Добавить папку")
        self.add_btn.setObjectName("Primary")
        self.add_btn.clicked.connect(self.add_library)
        top_h.addWidget(self.add_btn)

        self.fullscreen_btn = QPushButton("⛶")
        self.fullscreen_btn.setObjectName("Ghost")
        self.fullscreen_btn.setFixedWidth(38)
        self.fullscreen_btn.setToolTip("Полноэкранный режим (F11)")
        self.fullscreen_btn.clicked.connect(self.toggle_fullscreen)
        top_h.addWidget(self.fullscreen_btn)

        # --- gallery ------------------------------------------------------
        self.gallery = Gallery()
        self.gallery.set_icon_size(self.zoom_slider.value())
        self.gallery.photo_selected.connect(self._on_photo_selected)
        self.gallery.photo_activated.connect(self._open_viewer)
        self.gallery.context_action.connect(self._on_gallery_context_action)

        self.empty_label = QLabel()
        self.empty_label.setObjectName("EmptyState")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setWordWrap(True)
        self.empty_label.setVisible(False)

        # --- right column: filters over photo info ------------------------
        self.filters = FilterPanel()
        self.filters.changed.connect(self._schedule_refresh)
        self.photo_info = PhotoInfo(self.catalog)
        self.photo_info.tags_changed.connect(self._on_tags_changed)
        self.photo_info.open_requested.connect(self._open_viewer)
        self.photo_info.explorer_requested.connect(self._show_in_explorer_by_id)

        right = QWidget()
        right.setObjectName("RightPanel")
        right.setMinimumWidth(300)
        right_l = QVBoxLayout(right)
        right_l.setContentsMargins(0, 0, 0, 0)
        right_l.setSpacing(0)

        right_split = QSplitter(Qt.Orientation.Vertical)
        right_split.addWidget(self.filters)
        right_split.addWidget(self.photo_info)
        right_split.setStretchFactor(0, 0)
        right_split.setStretchFactor(1, 1)
        right_split.setSizes([240, 560])
        right_l.addWidget(right_split)

        # --- assembly -----------------------------------------------------
        center = QWidget()
        center_l = QVBoxLayout(center)
        center_l.setContentsMargins(0, 0, 0, 0)
        center_l.setSpacing(0)
        center_l.addWidget(top)
        center_l.addWidget(self.gallery, 1)
        center_l.addWidget(self.empty_label, 1)

        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        main_splitter.addWidget(self.sidebar)
        main_splitter.addWidget(center)
        main_splitter.addWidget(right)
        main_splitter.setStretchFactor(0, 0)
        main_splitter.setStretchFactor(1, 1)
        main_splitter.setStretchFactor(2, 0)
        main_splitter.setSizes([250, 800, 330])
        main_splitter.setChildrenCollapsible(False)
        self.setCentralWidget(main_splitter)

        # --- status bar ---------------------------------------------------
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Готово")
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(200)
        self.progress.setFormat("%v / %m")
        self.progress.setVisible(False)
        self.statusBar().addPermanentWidget(self.progress)
        self.count_label = QLabel()
        self.count_label.setObjectName("Muted")
        self.statusBar().addPermanentWidget(self.count_label)

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&Файл")
        file_menu.addAction(self._action("Добавить папку…", self.add_library,
                                         QKeySequence.StandardKey.Open))
        file_menu.addAction(self._action("Обновить библиотеки", self.rescan_all,
                                         QKeySequence("F5")))
        file_menu.addAction(self._action("Пересоздать миниатюры…", self.regenerate_thumbnails))
        file_menu.addSeparator()
        file_menu.addAction(self._action("Выход", self.close,
                                         QKeySequence.StandardKey.Quit))

        view_menu = self.menuBar().addMenu("&Просмотр")
        view_menu.addAction(self._action("Открыть фотографию", self._open_current, 
                                         QKeySequence("Return")))
        view_menu.addAction(self._action("Полноэкранный режим", self.toggle_fullscreen,
                                         QKeySequence("F11")))
        view_menu.addAction(self._action("Найти", self._focus_search,
                                         QKeySequence.StandardKey.Find))

        tags_menu = self.menuBar().addMenu("&Теги")
        tags_menu.addAction(self._action("Менеджер тегов…", self.open_tag_manager,
                                         QKeySequence("Ctrl+T")))
        tags_menu.addAction(self._action("Назначить теги…", self._assign_tags_current,
                                         QKeySequence("Ctrl+Shift+T")))

        help_menu = self.menuBar().addMenu("&Справка")
        help_menu.addAction(self._action("ExifTool…", self._show_exiftool_info))
        help_menu.addAction(self._action("О программе", self._about))

    def _action(self, text: str, slot, shortcut=None) -> QAction:
        action = QAction(text, self)
        if shortcut is not None:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(slot)
        return action

    def _build_shortcuts(self) -> None:
        escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        escape.activated.connect(self._on_escape)

    def _apply_theme(self) -> None:
        qss = load_stylesheet()
        if qss:
            self.setStyleSheet(qss)

    # ---------------------------------------------------------- fullscreen
    def toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _on_escape(self) -> None:
        if self.isFullScreen():
            self.showNormal()

    # -------------------------------------------------------------- actions
    def add_library(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Выберите папку с фотографиями")
        if not directory:
            return

        existing = self.catalog.find_library_by_path(directory)
        if existing is not None:
            # Already known — just refresh it instead of adding a duplicate.
            self.sidebar.reload(keep_selection=True)
            self._start_scan([existing.id])
            return

        overlap = self.catalog.find_overlapping_library(directory)
        if overlap is not None:
            self._info(
                "Папка уже в библиотеке",
                f"Выбранная папка пересекается с библиотекой «{overlap.name}».\n\n"
                f"{overlap.path}\n\n"
                "Выберите папку вне существующих библиотек, чтобы не "
                "индексировать одни и те же файлы дважды.",
            )
            return

        library = self.catalog.add_library(directory)
        self.sidebar.reload(keep_selection=True)
        self._start_scan([library.id])

    def rescan_all(self) -> None:
        libraries = self.catalog.list_libraries()
        if not libraries:
            self._info("Библиотека", "Сначала добавьте папку с фотографиями.")
            return
        self._start_scan([lib.id for lib in libraries])

    def rescan_library(self, library_id: int) -> None:
        self._start_scan([library_id])

    def open_library(self, library_id: int) -> None:
        library = self.catalog.get_library(library_id)
        if library and Path(library.path).is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(library.path))
        else:
            self._info("Библиотека", "Папка недоступна.")

    def relocate_library(self, library_id: int) -> None:
        """Point a library at a moved folder (e.g. a different drive letter)."""
        library = self.catalog.get_library(library_id)
        if library is None:
            return
        if self._is_scanning():
            self._info(
                "Сканирование",
                "Дождитесь завершения сканирования или остановите его.",
            )
            return

        directory = QFileDialog.getExistingDirectory(
            self, "Укажите новое расположение библиотеки", library.path
        )
        if not directory:
            return

        overlap = self.catalog.find_overlapping_library(directory)
        if overlap is not None and overlap.id != library_id:
            self._info(
                "Папка уже в библиотеке",
                f"Выбранная папка пересекается с библиотекой «{overlap.name}».",
            )
            return

        remapped = self.catalog.relocate_library(library_id, directory)
        self.sidebar.reload(keep_selection=True)
        self.statusBar().showMessage(f"Обновлено путей: {remapped}", 8000)
        self._start_scan([library_id])

    def remove_library(self, library_id: int) -> None:
        library = self.catalog.get_library(library_id)
        if library is None:
            return
        if self._is_scanning():
            self._info(
                "Сканирование",
                "Дождитесь завершения сканирования или остановите его.",
            )
            return
        confirm = QMessageBox.question(
            self,
            "Убрать библиотеку",
            f"Убрать «{library.name}» из библиотеки?\n\n"
            "Фотографии на диске не будут изменены или удалены.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self.catalog.remove_library(library_id)
        if self._library_id == library_id:
            self._on_section_selected("all")
            self.sidebar.select_section("all")
        self.sidebar.reload(keep_selection=True)
        self._reload_camera_models()
        self._refresh_gallery()

    def regenerate_thumbnails(self) -> None:
        libraries = self.catalog.list_libraries()
        if not libraries:
            self._info("Миниатюры", "Сначала добавьте папку с фотографиями.")
            return
        if self._is_scanning():
            self._info(
                "Сканирование",
                "Дождитесь завершения сканирования или остановите его.",
            )
            return
        confirm = QMessageBox.question(
            self,
            "Пересоздать миниатюры",
            "Удалить кэш миниатюр и создать их заново?\n"
            "Это может занять время для больших архивов.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        removed = thumbnail.clear_cache()
        log.info("Cleared %s cached thumbnails", removed)
        self._start_scan([lib.id for lib in libraries], regenerate=True)

    def open_tag_manager(self) -> None:
        dialog = TagManagerDialog(self.catalog, self)
        dialog.exec()
        self.sidebar.reload(keep_selection=True)
        self._refresh_gallery()

    def _about(self) -> None:
        QMessageBox.about(
            self,
            "О Pazik Manager",
            "<b>Pazik Manager</b><br>"
            "Каталогизация и поиск фотографий для транспортных фотографов.<br><br>"
            "Версия 0.1.0 · MVP",
        )

    def _show_exiftool_info(self) -> None:
        from core import exiftool

        exiftool.reset_cache()  # allow detecting a freshly installed copy
        path = exiftool.get_exiftool_path()
        if path:
            message = (
                f"<b>ExifTool найден</b><br><code>{path}</code><br><br>"
                "Метаданные RAW-файлов и встроенные превью читаются через ExifTool."
            )
            QMessageBox.information(self, "ExifTool", message)
            return

        choice = QMessageBox.question(
            self,
            "ExifTool не найден",
            "JPEG, PNG и TIFF работают без ExifTool, но для RAW-файлов "
            "(CR2/CR3, NEF, ARW, DNG и др.) он нужен.\n\n"
            "Установить ExifTool сейчас через winget?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if choice == QMessageBox.StandardButton.Yes:
            self._install_exiftool()

    def _install_exiftool(self) -> None:
        """Install ExifTool via winget, then re-detect it."""
        import shutil
        import subprocess

        winget = shutil.which("winget")
        if not winget:
            QMessageBox.information(
                self,
                "ExifTool",
                "winget не найден. Установите ExifTool вручную:<br><br>"
                "• Скачайте <i>exiftool-…_64.zip</i> с exiftool.org<br>"
                "• Положите <code>exiftool.exe</code> в <code>resources/tools/</code> "
                "рядом с приложением<br>"
                "• Перезапустите приложение",
            )
            return

        self.statusBar().showMessage("Установка ExifTool…")
        QApplication.processEvents()
        try:
            result = subprocess.run(
                [
                    winget,
                    "install",
                    "--id",
                    "OliverBetz.ExifTool",
                    "--accept-source-agreements",
                    "--accept-package-agreements",
                    "--disable-interactivity",
                ],
                capture_output=True,
                text=True,
                timeout=600,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            QMessageBox.warning(self, "ExifTool", f"Не удалось запустить winget:<br>{exc}")
            return

        from core import exiftool

        exiftool.reset_cache()
        if exiftool.is_available():
            self.statusBar().showMessage(
                f"ExifTool установлен: {exiftool.get_exiftool_path()}", 8000
            )
            QMessageBox.information(
                self,
                "ExifTool",
                "ExifTool установлен.<br><br>"
                "Выполните <b>Файл → Пересоздать миниатюры</b> или "
                "пересканируйте библиотеки, чтобы прочитать RAW-файлы.",
            )
        else:
            QMessageBox.warning(
                self,
                "ExifTool",
                "Установка завершилась, но ExifTool не найден.<br><br>"
                f"Вывод winget:<br><code>{(result.stdout or result.stderr or '')[-800:]}</code>"
                "<br><br>Перезапустите приложение.",
            )

    def _focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()

    # -------------------------------------------------------------- scanning
    def _is_scanning(self) -> bool:
        return bool(self._active_worker and self._active_worker.isRunning())

    def _start_scan(self, library_ids: list[int], regenerate: bool = False) -> None:
        pending = [(lib_id, regenerate) for lib_id in library_ids]
        if self._is_scanning():
            # Queue instead of silently dropping the request.
            self._scan_queue.extend(pending)
            self._set_scan_ui(True)
            return
        self._scan_queue = pending
        self._run_next_scan()

    def _run_next_scan(self) -> None:
        if not self._scan_queue:
            self._finish_scan_ui()
            return
        library_id, regenerate = self._scan_queue.pop(0)
        library = self.catalog.get_library(library_id)
        if library is None:
            self._run_next_scan()
            return

        self._set_scan_ui(True)
        self.progress.setRange(0, 0)  # indeterminate until the first tick
        self.statusBar().showMessage(f"Сканирование: {library.name}")

        worker = ScanWorker(
            str(self.catalog.db.path),
            library,
            regenerate_thumbnails=regenerate,
        )
        worker.progress.connect(self._on_scan_progress)
        worker.finished_scan.connect(self._on_scan_finished)
        worker.failed.connect(self._on_scan_failed)
        worker.finished.connect(self._on_worker_finished)
        self._active_worker = worker
        worker.start()

    def _on_scan_progress(self, done: int, total: int, path: str) -> None:
        if total:
            self.progress.setRange(0, total)
            self.progress.setValue(done)
        self.statusBar().showMessage(f"Сканирование… {done} / {total}")

    def _on_scan_finished(self, result) -> None:
        if self._closing:
            return
        self.sidebar.reload(keep_selection=True)
        self._reload_camera_models()
        self._refresh_gallery()

        if getattr(result, "cancelled", False):
            message = f"Сканирование остановлено · найдено: {result.found}"
        else:
            message = (
                f"Готово · найдено: {result.found} · новых: {result.added} · "
                f"обновлено: {result.updated} · отсутствует: {result.missing}"
            )
            if result.errors:
                message += f" · ошибок: {result.errors}"
        self.statusBar().showMessage(message, 15000)

    def _on_scan_failed(self, message: str) -> None:
        self.statusBar().showMessage("Сканирование завершилось ошибкой", 10000)
        QMessageBox.warning(self, "Ошибка сканирования", message)

    def _on_worker_finished(self) -> None:
        """Thread lifecycle: clear the reference before starting the next scan."""
        worker = self._active_worker
        self._active_worker = None
        if worker is not None:
            worker.deleteLater()
        if self._closing:
            return
        self._run_next_scan()

    def _finish_scan_ui(self) -> None:
        if self._closing:
            return
        self.progress.setVisible(False)
        self.progress.setRange(0, 100)
        self.rescan_btn.setText("Обновить")
        self._refresh_gallery()

    def _set_scan_ui(self, scanning: bool) -> None:
        self.progress.setVisible(scanning)
        self.rescan_btn.setText("Остановить" if scanning else "Обновить")

    def _on_rescan_clicked(self) -> None:
        if self._is_scanning():
            self._cancel_scan()
        else:
            self.rescan_all()

    def _cancel_scan(self) -> None:
        self._scan_queue.clear()
        if self._active_worker is not None:
            self._active_worker.request_cancel()
            self.statusBar().showMessage("Остановка сканирования…")

    # ------------------------------------------------------------ selection
    def _on_library_selected(self, library_id: int) -> None:
        self._library_id = library_id
        self._tag_id = None
        self._section = ""
        self._schedule_refresh()

    def _on_tag_selected(self, tag_id: int) -> None:
        self._tag_id = tag_id
        self._library_id = None
        self._section = ""
        self._schedule_refresh()

    def _on_section_selected(self, section: str) -> None:
        self._section = section
        self._library_id = None
        self._tag_id = None
        self._schedule_refresh()

    def _on_search_changed(self, text: str) -> None:
        self._search_text = text
        self._schedule_refresh()

    def _on_zoom_changed(self, value: int) -> None:
        self.gallery.set_icon_size(value)

    def _on_photo_selected(self, photo_id: int) -> None:
        photo = self.catalog.get_photo(photo_id)
        self.photo_info.set_photo(photo)

    def _on_tags_changed(self) -> None:
        self.sidebar.reload(keep_selection=True)
        self._refresh_gallery()

    def _schedule_refresh(self) -> None:
        self._refresh_timer.start()

    # --------------------------------------------------------------- query
    def _current_query(self) -> PhotoQuery:
        query = PhotoQuery()
        query.library_id = self._library_id
        query.text = self._search_text or None
        if self._tag_id is not None:
            query.tag_ids = [self._tag_id]
        if self._section == "untagged":
            query.untagged = True
        elif self._section == "missing":
            query.missing = True

        values = self.filters.values()
        if values["date_from"]:
            query.date_from = values["date_from"] + "T00:00:00"
        if values["date_to"]:
            query.date_to = values["date_to"]
        query.camera_model = values["camera_model"]
        if values["missing"]:
            query.missing = True
        if values["untagged"]:
            query.untagged = True
        return query

    def _refresh_gallery(self) -> None:
        if self._closing:
            return
        self._refresh_timer.stop()
        photos = self.catalog.search(self._current_query())
        self._photos = photos
        self._photos_by_id = {photo.id: photo for photo in photos}

        keep_id = self.gallery.selected_photo_id()
        self.gallery.set_photos(photos, keep_id=keep_id)

        if self.gallery.selected_photo_id() is None:
            self.photo_info.set_photo(None)
        else:
            self._on_photo_selected(self.gallery.selected_photo_id())

        self._update_empty_state()
        self._update_count(len(photos))

    def _update_empty_state(self) -> None:
        has_photos = bool(self._photos)
        self.gallery.setVisible(has_photos)
        self.empty_label.setVisible(not has_photos)
        if has_photos:
            return

        if not self.catalog.list_libraries():
            self.empty_label.setText(
                "Библиотека пуста\n\nДобавьте папку с фотографиями, чтобы начать"
            )
        elif self._search_text or self._tag_id is not None or self._section not in (
            "all",
            "",
        ) or any(self.filters.values().values()):
            self.empty_label.setText(
                "Ничего не найдено\n\nИзмените запрос или сбросьте фильтры"
            )
        else:
            self.empty_label.setText(
                "В библиотеке пока нет фотографий\n\nНажмите «Обновить» для сканирования"
            )

    def _update_count(self, shown: int) -> None:
        total = self.catalog.count_photos()
        self.count_label.setText(f"Показано: {shown} из {total}")

    def _reload_camera_models(self) -> None:
        self.filters.set_camera_models(self.catalog.distinct_camera_models())

    # ------------------------------------------------------------- viewer
    def _open_current(self) -> None:
        photo_id = self.gallery.selected_photo_id()
        if photo_id is not None:
            self._open_viewer(photo_id)

    def _open_viewer(self, photo_id: int) -> None:
        photo = self.catalog.get_photo(photo_id)
        if photo is None:
            return

        photos = self._photos or [photo]
        index = next((i for i, p in enumerate(photos) if p.id == photo_id), None)
        if index is None:
            photos, index = [photo], 0

        viewer = ImageViewer(photos, index, self)
        viewer.exec()

    def _show_in_explorer_by_id(self, photo_id: int) -> None:
        photo = self.catalog.get_photo(photo_id)
        if photo is None:
            return
        path = Path(photo.path)
        if not path.exists():
            self._info("Файл", "Файл не найден на диске.")
            return
        if sys.platform == "win32":
            import subprocess

            subprocess.Popen(["explorer", "/select,", str(path)])
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

    def _on_gallery_context_action(self, action: str, photo_id: int) -> None:
        if action == ACTION_OPEN:
            self._open_viewer(photo_id)
        elif action == ACTION_EXPLORER:
            self._show_in_explorer_by_id(photo_id)
        elif action == ACTION_COPY_PATH:
            photo = self.catalog.get_photo(photo_id)
            if photo is not None:
                QApplication.clipboard().setText(photo.path)
                self.statusBar().showMessage("Путь скопирован", 3000)

    def _assign_tags_current(self) -> None:
        photo_id = self.gallery.selected_photo_id()
        if photo_id is None:
            return
        photo = self.catalog.get_photo(photo_id)
        if photo is None:
            return
        from .tag_editor import TagAssignDialog

        dialog = TagAssignDialog(self.catalog, photo, self)
        if dialog.exec():
            self._on_photo_selected(photo_id)
            self._on_tags_changed()

    # --------------------------------------------------------------- helpers
    def _info(self, title: str, message: str) -> None:
        QMessageBox.information(self, title, message)

    # --------------------------------------------------------------- events
    def closeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        self._closing = True
        self._refresh_timer.stop()
        if self._is_scanning():
            self._cancel_scan()
            if not self._active_worker.wait(8000):
                self._closing = False
                QMessageBox.warning(
                    self,
                    "Сканирование",
                    "Сканирование ещё выполняется. Дождитесь его завершения.",
                )
                event.ignore()
                return
        self.settings.setValue("gallery/icon_size", self.zoom_slider.value())
        self.catalog.close()
        super().closeEvent(event)