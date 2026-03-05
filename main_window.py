from __future__ import annotations

import glob
import itertools
import re
from dataclasses import dataclass, field
from pathlib import Path

from PyQt6.QtCore import Qt, QPointF, QRectF, QThread, pyqtSignal, QObject
from PyQt6.QtGui import QColor, QCursor, QKeySequence, QDragEnterEvent, QDropEvent, QShortcut, QCloseEvent, QImage
from PyQt6.QtWidgets import (
    QMainWindow,
    QToolBar,
    QSplitter,
    QFileDialog,
    QMenu,
    QMessageBox,
    QDockWidget,
    QListWidget,
    QLabel,
    QWidget,
    QVBoxLayout,
)

from ass_parser import AssFile, LabelDialogue
from video_widget import VideoFrameWidget, VideoSetupWorker, detect_fps, _get_video_dimensions, extract_frame_as_image
from gallery_widget import GalleryPanel, LabelGroup, compute_label_groups, _crop_to_labels_image, _best_representative_time
from label_toolbar import LabelToolbar

_VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".webm"}


def _anchor_for_alignment(rect: QRectF, alignment: int) -> QPointF:
    """Return the anchor point of *rect* for the given ASS numpad alignment."""
    h_align = ((alignment - 1) % 3) + 1
    v_group = (alignment - 1) // 3
    ax = rect.x() if h_align == 1 else (rect.right() if h_align == 3 else rect.center().x())
    ay = rect.bottom() if v_group == 0 else (rect.center().y() if v_group == 1 else rect.top())
    return QPointF(ax, ay)


def _status_msg(window: QMainWindow, msg: str, timeout: int = 3000) -> None:
    bar = window.statusBar()
    if bar:
        bar.showMessage(msg, timeout)


def _natural_sort_key(path: str) -> list:
    name = Path(path).name.lower()
    return [int(c) if c.isdigit() else c for c in re.split(r'(\d+)', name)]


@dataclass
class PreloadedFileData:
    fps: float
    dims: tuple[int, int] | None
    initial_frame: QImage | None
    ass: AssFile | None
    groups: list[LabelGroup]
    thumbnails: dict[int, QImage] = field(default_factory=dict)


class FolderPreloadWorker(QObject):
    """Processes all folder files sequentially on a background thread."""
    file_ready = pyqtSignal(str, object)  # path, PreloadedFileData
    all_done = pyqtSignal()

    def __init__(self, file_paths: list[str]):
        super().__init__()
        self._file_paths = file_paths
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        for path in self._file_paths:
            if self._cancelled:
                break

            fps = detect_fps(path)
            if self._cancelled:
                break
            dims = _get_video_dimensions(path)
            if self._cancelled:
                break
            initial_frame = extract_frame_as_image(path, 0)
            if self._cancelled:
                break

            # Find matching .ass file
            video = Path(path)
            ass_file: AssFile | None = None
            exact = video.with_suffix(".ass")
            if exact.is_file():
                ass_file = AssFile(str(exact))
            else:
                candidates = sorted(video.parent.glob(f"{glob.escape(video.stem)}.*.ass"))
                if candidates:
                    ass_file = AssFile(str(candidates[0]))

            groups: list[LabelGroup] = []
            thumbnails: dict[int, QImage] = {}

            if ass_file and not self._cancelled:
                groups = compute_label_groups(ass_file.labels)
                for i, group in enumerate(groups):
                    if self._cancelled:
                        break
                    img = extract_frame_as_image(path, group.representative_time)
                    if img and not img.isNull():
                        cropped = _crop_to_labels_image(
                            img, group,
                            ass_file.play_res_x, ass_file.play_res_y,
                            ass_file.label_font_name, ass_file.label_font_size,
                            ass_file.label_alignment,
                        )
                        thumbnails[i] = cropped

            if not self._cancelled:
                data = PreloadedFileData(
                    fps=fps,
                    dims=dims,
                    initial_frame=initial_frame,
                    ass=ass_file,
                    groups=groups,
                    thumbnails=thumbnails,
                )
                self.file_ready.emit(path, data)

        self.all_done.emit()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Sub Label Pos")
        self.resize(1280, 720)
        self.setAcceptDrops(True)

        self._ass: AssFile | None = None
        self._groups: list[LabelGroup] = []
        self._group_index: int = -1
        self._video_path: str | None = None
        self._style_clipboard: tuple[int, int | None] | None = None
        self.__dirty: bool = False
        self._folder_files: list[str] = []
        self._folder_index: int = -1
        self._suppress_resize: bool = False

        # Async video setup
        self._setup_thread: QThread | None = None
        self._setup_worker: VideoSetupWorker | None = None

        # Folder pre-loading
        self._preloaded: dict[str, PreloadedFileData] = {}
        self._preload_thread: QThread | None = None
        self._preload_worker: FolderPreloadWorker | None = None

        # ── Files sidebar (hidden by default) ──
        self._files_dock = QDockWidget("Files", self)
        self._files_dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        dock_content = QWidget()
        dock_layout = QVBoxLayout(dock_content)
        dock_layout.setContentsMargins(6, 6, 6, 6)
        dock_layout.setSpacing(4)
        self._folder_path_label = QLabel()
        self._folder_path_label.setStyleSheet("color: #888; font-size: 11px;")
        self._folder_path_label.setWordWrap(False)
        dock_layout.addWidget(self._folder_path_label)
        self._folder_progress_label = QLabel()
        self._folder_progress_label.setStyleSheet("color: #ccc; font-size: 12px;")
        dock_layout.addWidget(self._folder_progress_label)
        self._file_list = QListWidget()
        self._file_list.setStyleSheet("""
            QListWidget {
                background: #252525;
                color: #ccc;
                border: none;
                font-size: 12px;
            }
            QListWidget::item {
                padding: 4px 6px;
            }
            QListWidget::item:selected {
                background: #4a9eff;
                color: #fff;
            }
        """)
        dock_layout.addWidget(self._file_list)
        dock_content.setStyleSheet("background: #2d2d2d;")
        self._files_dock.setWidget(dock_content)
        self._files_dock.setMinimumWidth(180)
        self._files_dock.setMaximumWidth(320)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self._files_dock)
        self._files_dock.hide()
        self._file_list.currentRowChanged.connect(self._on_file_list_clicked)

        # Layout: splitter with player on top, gallery on bottom
        self._player = VideoFrameWidget()
        self._gallery = GalleryPanel()

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._player)
        splitter.addWidget(self._gallery)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        self.setCentralWidget(splitter)

        # Floating toolbar (child of player so it overlays the video)
        self._toolbar = LabelToolbar(self._player)

        # Main toolbar
        tb = QToolBar("Main")
        tb.setMovable(False)
        self.addToolBar(tb)
        tb.addAction("Open Video", self._open_video)
        tb.addAction("Open Folder", self._open_folder)
        tb.addSeparator()
        tb.addAction("Open ASS", self._open_ass)
        tb.addAction("Save ASS", self._save_ass)

        # Shortcuts — frame stepping (arrow keys)
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, lambda: self._player.step_frame(1))
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, lambda: self._player.step_frame(-1))
        # Gallery navigation (Ctrl+arrow keys)
        QShortcut(QKeySequence("Ctrl+Right"), self, lambda: self._goto_group(self._group_index + 1))
        QShortcut(QKeySequence("Ctrl+Left"), self, lambda: self._goto_group(self._group_index - 1))
        QShortcut(QKeySequence.StandardKey.Save, self, self._save_ass)
        # File navigation (Ctrl+Shift+arrow keys)
        QShortcut(QKeySequence("Ctrl+Shift+Right"), self, self._next_file)
        QShortcut(QKeySequence("Ctrl+Shift+Left"), self, self._prev_file)
        # Delete selected labels
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self, self._on_delete)

        # ── Connect signals ──

        # Video widget signals
        self._player.label_moved.connect(self._on_label_moved)
        self._player.label_selected.connect(self._on_label_selected)
        self._player.selection_cleared.connect(self._on_selection_cleared)
        self._player.edit_requested.connect(self._on_edit_requested)
        self._player.text_edited.connect(self._on_text_edited)
        self._player.context_menu_requested.connect(self._on_context_menu)
        self._player.empty_context_menu_requested.connect(self._on_empty_context_menu)

        # Gallery signals
        self._gallery.group_selected.connect(self._on_group_selected)
        self._gallery.group_right_clicked.connect(self._on_gallery_context_menu)

        # Toolbar signals
        self._toolbar.duplicate_clicked.connect(self._on_duplicate)
        self._toolbar.delete_clicked.connect(self._on_delete)
        self._toolbar.font_size_changed.connect(self._on_font_size_changed)
        self._toolbar.alignment_changed.connect(self._on_alignment_changed)
        self._toolbar.copy_style_clicked.connect(self._on_copy_style)
        self._toolbar.paste_style_clicked.connect(self._on_paste_style)

    # ── Dirty flag property ──

    @property
    def _dirty(self) -> bool:
        return self.__dirty

    @_dirty.setter
    def _dirty(self, value: bool) -> None:
        self.__dirty = value
        if value and self._video_path:
            self._preloaded.pop(self._video_path, None)
        self._update_window_title()

    def _update_window_title(self) -> None:
        if self._video_path:
            name = Path(self._video_path).name
            self.setWindowTitle(f"Sub Label Pos — {name} [*]")
        else:
            self.setWindowTitle("Sub Label Pos [*]")
        self.setWindowModified(self.__dirty)

    # ── File loading ──

    def _open_video(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Video", "", "Video Files (*.mkv *.mp4 *.avi *.webm);;All (*)"
        )
        if path:
            self._load_video(path)

    def _load_video(self, path: str, suppress_resize: bool = False) -> None:
        self._cancel_video_setup()
        self._video_path = path
        self._suppress_resize = suppress_resize
        self._player.set_video(path)
        _status_msg(self, "Loading...", 0)

        # Start async video setup
        self._setup_worker = VideoSetupWorker(path)
        self._setup_thread = QThread()
        self._setup_worker.moveToThread(self._setup_thread)
        self._setup_thread.started.connect(self._setup_worker.run)
        self._setup_worker.finished.connect(self._on_video_setup_done)
        self._setup_worker.finished.connect(self._setup_thread.quit)
        self._setup_thread.start()

    def _on_video_setup_done(self, fps: float, dims: object, frame: object) -> None:
        self._player.set_fps(fps)

        if isinstance(frame, QImage) and not frame.isNull():
            self._player.show_frame_from_image(frame)

        if not self._suppress_resize and dims is not None:
            vid_w, vid_h = dims
            screen_obj = self.screen()
            if screen_obj:
                screen = screen_obj.availableGeometry()
                max_w = int(screen.width() * 0.7)
                target_w = min(vid_w, max_w)
                aspect = vid_h / vid_w
                target_h = int(target_w * aspect) + 160  # gallery height
                target_h = min(target_h, int(screen.height() * 0.85))
                self.resize(target_w, target_h)
                x = screen.x() + (screen.width() - target_w) // 2
                y = screen.y() + (screen.height() - target_h) // 2
                self.move(x, y)

        # Auto-load matching .ass
        video = Path(self._video_path) if self._video_path else None
        if video:
            exact = video.with_suffix(".ass")
            if exact.is_file():
                self._load_ass(str(exact))
            else:
                candidates = sorted(video.parent.glob(f"{glob.escape(video.stem)}.*.ass"))
                if candidates:
                    self._load_ass(str(candidates[0]))

        self._update_window_title()
        _status_msg(self, f"Loaded: {self._video_path}")

    def _cancel_video_setup(self) -> None:
        if self._setup_thread and self._setup_thread.isRunning():
            self._setup_thread.quit()
            self._setup_thread.wait(2000)
        self._setup_worker = None
        self._setup_thread = None

    # ── Folder loading ──

    def _open_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Open Folder")
        if not folder:
            return
        self._cancel_folder_preload()
        def _has_ass_file(video: Path) -> bool:
            if video.with_suffix(".ass").is_file():
                return True
            return bool(sorted(video.parent.glob(f"{glob.escape(video.stem)}.*.ass")))

        files = [
            str(p) for p in Path(folder).iterdir()
            if p.is_file() and p.suffix.lower() in _VIDEO_EXTS
            and _has_ass_file(p)
        ]
        files.sort(key=_natural_sort_key)
        if not files:
            QMessageBox.information(self, "No Videos", "No video files with matching .ass subtitle files found in the selected folder.")
            return
        self._folder_files = files
        # Populate sidebar
        self._folder_path_label.setText(folder)
        self._file_list.blockSignals(True)
        self._file_list.clear()
        for f in files:
            self._file_list.addItem(Path(f).name)
        self._file_list.blockSignals(False)
        self._files_dock.show()
        self._switch_to_file(0)
        # Start pre-loading all files in background
        self._start_folder_preload(files)

    def _switch_to_file(self, index: int) -> None:
        if not self._folder_files:
            return
        index = max(0, min(index, len(self._folder_files) - 1))
        if index == self._folder_index:
            return
        # Dirty guard
        if self.__dirty and self._ass:
            reply = QMessageBox.question(
                self,
                "Unsaved Changes",
                "You have unsaved changes. Do you want to save before switching?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save,
            )
            if reply == QMessageBox.StandardButton.Save:
                self._save_ass()
            elif reply == QMessageBox.StandardButton.Cancel:
                # Restore sidebar selection to current file
                if self._folder_index >= 0:
                    self._file_list.blockSignals(True)
                    self._file_list.setCurrentRow(self._folder_index)
                    self._file_list.blockSignals(False)
                return
        self._folder_index = index
        path = self._folder_files[index]
        suppress = index > 0 or self._video_path is not None
        # Use pre-loaded data if available
        cached = self._preloaded.get(path)
        if cached:
            self._load_video_from_cache(path, cached, suppress)
        else:
            self._load_video(path, suppress_resize=suppress)
        # Update sidebar
        self._file_list.blockSignals(True)
        self._file_list.setCurrentRow(index)
        self._file_list.blockSignals(False)
        self._folder_progress_label.setText(f"{index + 1} / {len(self._folder_files)} files")

    def _next_file(self) -> None:
        if self._folder_files and self._folder_index < len(self._folder_files) - 1:
            self._switch_to_file(self._folder_index + 1)

    def _prev_file(self) -> None:
        if self._folder_files and self._folder_index > 0:
            self._switch_to_file(self._folder_index - 1)

    def _on_file_list_clicked(self, row: int) -> None:
        if row >= 0:
            self._switch_to_file(row)

    # ── Folder pre-loading ──

    def _start_folder_preload(self, file_paths: list[str]) -> None:
        self._preload_worker = FolderPreloadWorker(file_paths)
        self._preload_thread = QThread()
        self._preload_worker.moveToThread(self._preload_thread)
        self._preload_thread.started.connect(self._preload_worker.run)
        self._preload_worker.file_ready.connect(self._on_file_preloaded)
        self._preload_worker.all_done.connect(self._on_preload_done)
        self._preload_worker.all_done.connect(self._preload_thread.quit)
        self._preload_thread.start()

    def _on_file_preloaded(self, path: str, data: object) -> None:
        if not isinstance(data, PreloadedFileData):
            return
        self._preloaded[path] = data
        # Update sidebar to indicate ready (green text)
        for i, f in enumerate(self._folder_files):
            if f == path:
                item = self._file_list.item(i)
                if item:
                    item.setForeground(QColor("#6fc276"))
                break

    def _on_preload_done(self) -> None:
        _status_msg(self, "Folder pre-loading complete")

    def _cancel_folder_preload(self) -> None:
        if self._preload_worker:
            self._preload_worker.cancel()
        if self._preload_thread and self._preload_thread.isRunning():
            self._preload_thread.quit()
            self._preload_thread.wait(3000)
        self._preload_worker = None
        self._preload_thread = None
        self._preloaded.clear()

    def _load_video_from_cache(self, path: str, data: PreloadedFileData,
                               suppress_resize: bool) -> None:
        """Load a video file using pre-loaded data (near-instant)."""
        self._cancel_video_setup()
        self._video_path = path
        self._player.set_video(path)
        self._player.set_fps(data.fps)

        if data.initial_frame and not data.initial_frame.isNull():
            self._player.show_frame_from_image(data.initial_frame)

        if not suppress_resize and data.dims is not None:
            vid_w, vid_h = data.dims
            screen_obj = self.screen()
            if screen_obj:
                screen = screen_obj.availableGeometry()
                max_w = int(screen.width() * 0.7)
                target_w = min(vid_w, max_w)
                aspect = vid_h / vid_w
                target_h = int(target_w * aspect) + 160
                target_h = min(target_h, int(screen.height() * 0.85))
                self.resize(target_w, target_h)
                x = screen.x() + (screen.width() - target_w) // 2
                y = screen.y() + (screen.height() - target_h) // 2
                self.move(x, y)

        if data.ass:
            self._ass = data.ass
            self._dirty = False
            self._player.set_ass(self._ass)
            self._toolbar.hide()
            self._gallery.set_data_preloaded(
                path, data.ass, data.groups, data.thumbnails,
            )
            self._groups = self._gallery.groups
            if self._groups:
                self._goto_group(0)
        else:
            self._ass = None
            self._groups = []
            self._group_index = -1
            self._gallery.clear()

        self._update_window_title()
        _status_msg(self, f"Loaded: {path}")

    def _open_ass(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open ASS", "", "ASS Subtitles (*.ass);;All (*)"
        )
        if path:
            self._load_ass(path)

    def _load_ass(self, path: str) -> None:
        self._ass = AssFile(path)
        self._dirty = False
        self._player.set_ass(self._ass)
        self._toolbar.hide()
        _status_msg(self, f"Loaded {len(self._ass.labels)} labels from {path}")

        if self._video_path:
            self._rebuild_gallery()
            if self._groups:
                self._goto_group(0)
        else:
            self._groups = []
            self._group_index = -1
            self._player.show_time(self._player._current_time)

    def _save_ass(self) -> None:
        if not self._ass:
            _status_msg(self, "No ASS file loaded")
            return
        self._ass.save()
        self._dirty = False
        self._repopulate_cache()
        _status_msg(self, f"Saved: {self._ass.path}")

    def _repopulate_cache(self) -> None:
        """Rebuild the preload cache entry from current live state."""
        if not self._video_path or not self._ass:
            return
        thumbnails: dict[int, QImage] = {}
        for i, thumb in enumerate(self._gallery._thumbnails):
            pm = thumb._image_label.pixmap()
            if pm and not pm.isNull():
                thumbnails[i] = pm.toImage()
        self._preloaded[self._video_path] = PreloadedFileData(
            fps=self._player._fps,
            dims=None,
            initial_frame=None,
            ass=self._ass,
            groups=list(self._groups),
            thumbnails=thumbnails,
        )

    # ── Gallery ──

    def _rebuild_gallery(self) -> None:
        """Full gallery rebuild. Only used on initial ASS load."""
        if self._video_path and self._ass:
            self._gallery.set_data(self._video_path, self._ass)
            self._groups = self._gallery.groups

    def _group_index_for_label(self, label: LabelDialogue) -> int:
        """Find which group a label belongs to, or -1."""
        for i, group in enumerate(self._groups):
            if label in group.labels:
                return i
        return -1

    def _goto_group(self, index: int) -> None:
        if not self._groups:
            return
        index = max(0, min(index, len(self._groups) - 1))
        self._group_index = index
        group = self._groups[index]
        self._player.show_time(group.representative_time)
        self._player.prefetch_around(group.representative_time)
        self._gallery.select_group(index)

    def _on_group_selected(self, index: int) -> None:
        if index == self._group_index:
            return
        self._group_index = index
        if 0 <= index < len(self._groups):
            t = self._groups[index].representative_time
            self._player.show_time(t)
            self._player.prefetch_around(t)

    # ── Drag and drop ──

    def dragEnterEvent(self, event: QDragEnterEvent | None) -> None:  # type: ignore[override]
        if not event:
            return
        mime = event.mimeData()
        if mime and mime.hasUrls():
            for url in mime.urls():
                if url.isLocalFile() and Path(url.toLocalFile()).suffix.lower() in _VIDEO_EXTS:
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event: QDropEvent | None) -> None:  # type: ignore[override]
        if not event:
            return
        mime = event.mimeData()
        if mime:
            for url in mime.urls():
                if url.isLocalFile():
                    path = url.toLocalFile()
                    if Path(path).suffix.lower() in _VIDEO_EXTS:
                        self._load_video(path)
                        event.acceptProposedAction()
                        return
        event.ignore()

    # ── Label interaction ──

    def _on_label_moved(self, label: LabelDialogue, new_x: int, new_y: int) -> None:
        if self._ass:
            self._ass.set_label_position(label, new_x, new_y)
            self._dirty = True
            _status_msg(self, f"Moved \"{label.text}\" to ({new_x}, {new_y})")
        self._update_toolbar_position()

    def _on_label_selected(self, label: LabelDialogue) -> None:
        selected = self._player.selected_labels()
        multi = len(selected) > 1
        font_size = label.font_size if label.font_size is not None else (
            self._ass.label_font_size if self._ass else 36
        )
        if multi:
            alignments = {lb.alignment for lb in selected}
            effective_alignment = alignments.pop() if len(alignments) == 1 else None
        else:
            effective_alignment = label.alignment
        self._toolbar.set_multi_mode(multi)
        self._toolbar.show_for_label(font_size, effective_alignment)
        self._update_toolbar_position()

    def _on_selection_cleared(self) -> None:
        self._toolbar.hide()

    def _update_toolbar_position(self) -> None:
        """Position toolbar above the first selected label's rect."""
        selected = self._player.selected_labels()
        if not selected:
            return
        label = selected[0]
        rect = self._player._label_rects.get(label.line_index)
        if rect:
            self._toolbar.position_above(rect.center().x(), rect.top())

    # ── Toolbar actions ──

    def _on_duplicate(self) -> None:
        if not self._ass:
            return
        selected = self._player.selected_labels()
        if not selected:
            return
        label = selected[0]
        new_label = self._ass.duplicate_label(label)
        # Add to same group (same time range)
        gi = self._group_index_for_label(label)
        if gi >= 0:
            self._groups[gi].labels.append(new_label)
            self._gallery.refresh_thumbnail(gi)
        self._player.show_time(self._player._current_time)
        self._dirty = True
        _status_msg(self, f"Duplicated \"{label.text}\"")

    def _on_delete(self) -> None:
        if not self._ass:
            return
        selected = self._player.selected_labels()
        if not selected:
            return
        self._toolbar.hide()
        self._player.clear_selection()
        # Find affected group before deleting
        gi = self._group_index_for_label(selected[0])
        # Remove from ASS
        self._ass.delete_labels(selected)
        # Update the group
        if gi >= 0:
            group = self._groups[gi]
            for lb in selected:
                if lb in group.labels:
                    group.labels.remove(lb)
            if not group.labels:
                self._remove_group_and_advance(gi)
            else:
                group.representative_time = _best_representative_time(group.labels)
                self._gallery.refresh_thumbnail(gi)
                self._goto_group(gi)
        else:
            self._player.show_time(self._player._current_time)
        count = len(selected)
        self._dirty = True
        _status_msg(self, f"Deleted {count} label{'s' if count > 1 else ''}")

    def _delete_group(self, gi: int) -> None:
        """Delete all labels in a group and remove it from the gallery."""
        if not self._ass or not (0 <= gi < len(self._groups)):
            return
        self._toolbar.hide()
        self._player.clear_selection()
        group = self._groups[gi]
        count = len(group.labels)
        self._ass.delete_labels(list(group.labels))
        group.labels.clear()
        self._remove_group_and_advance(gi)
        self._dirty = True
        _status_msg(self, f"Deleted {count} label{'s' if count > 1 else ''}")

    def _remove_group_and_advance(self, gi: int) -> None:
        """Remove an empty group from the gallery and navigate to the next one."""
        self._gallery.remove_group(gi)
        if not self._groups:
            self._group_index = -1
            self._player.show_time(self._player._current_time)
            return
        # Advance: prefer same index (now the next group), else clamp
        next_gi = min(gi, len(self._groups) - 1)
        self._goto_group(next_gi)

    def _on_font_size_changed(self, size: int) -> None:
        if not self._ass:
            return
        for label in self._player.selected_labels():
            self._ass.set_label_font_size(label, size)
        self._dirty = True
        self._player.update()

    def _on_alignment_changed(self, new_alignment: int) -> None:
        if not self._ass:
            return
        selected = self._player.selected_labels()
        if not selected:
            return
        for label in selected:
            font = self._player._font_for_label(label)
            rect = self._player._compute_rect(label, font)
            new_anchor = _anchor_for_alignment(rect, new_alignment)
            new_x, new_y = self._player._widget_to_ass(new_anchor.x(), new_anchor.y())
            self._ass.set_label_alignment(label, new_alignment)
            self._ass.set_label_position(label, new_x, new_y)
        self._dirty = True
        self._player.update()
        self._update_toolbar_position()

    def _on_copy_style(self) -> None:
        selected = self._player.selected_labels()
        if not selected:
            return
        label = selected[0]
        font_size = label.font_size if label.font_size is not None else (
            self._ass.label_font_size if self._ass else 36
        )
        self._style_clipboard = (font_size, label.alignment)
        _status_msg(self, f"Copied style (font size: {font_size}, alignment: {label.alignment})")

    def _on_paste_style(self) -> None:
        if not self._ass or self._style_clipboard is None:
            return
        font_size, alignment = self._style_clipboard
        for label in self._player.selected_labels():
            self._ass.set_label_font_size(label, font_size)
            if alignment is not None:
                font = self._player._font_for_label(label)
                rect = self._player._compute_rect(label, font)
                new_anchor = _anchor_for_alignment(rect, alignment)
                new_x, new_y = self._player._widget_to_ass(new_anchor.x(), new_anchor.y())
                self._ass.set_label_alignment(label, alignment)
                self._ass.set_label_position(label, new_x, new_y)
        self._dirty = True
        self._player.update()
        self._toolbar.show_for_label(font_size, alignment)
        self._update_toolbar_position()
        _status_msg(self, f"Pasted style (font size: {font_size}, alignment: {alignment})")

    # ── Inline text editing ──

    def _on_edit_requested(self, label: LabelDialogue) -> None:
        self._toolbar.hide()
        self._player.start_editing(label)

    def _on_text_edited(self, label: LabelDialogue, new_text: str) -> None:
        if not self._ass:
            return
        self._ass.set_label_text(label, new_text)
        self._dirty = True
        self._player.show_time(self._player._current_time)
        # Update just the text on the affected thumbnail
        gi = self._group_index_for_label(label)
        if gi >= 0 and gi < len(self._gallery._thumbnails):
            texts = [lb.text for lb in self._groups[gi].labels]
            self._gallery._thumbnails[gi].update_texts(texts)
        _status_msg(self, f"Updated text to \"{new_text}\"")

    # ── Context menus ──

    def _on_context_menu(self, pos: QPointF) -> None:
        menu = QMenu(self)
        selected = self._player.selected_labels()

        menu.addAction("Duplicate", self._on_duplicate)
        menu.addAction("Delete", self._on_delete)
        menu.addSeparator()

        if 2 <= len(selected) <= 3:
            self._build_merge_submenu(menu, selected)

        if len(selected) == 1:
            menu.addAction("Edit Text", lambda: self._on_edit_requested(selected[0]))
            menu.addAction("Copy Style", self._on_copy_style)
        if self._style_clipboard is not None:
            menu.addAction("Paste Style", self._on_paste_style)

        # Map position from player widget to global
        global_pos = self._player.mapToGlobal(pos.toPoint())
        menu.exec(global_pos)

    def _on_gallery_context_menu(self, index: int) -> None:
        if not (0 <= index < len(self._groups)):
            return

        menu = QMenu(self)
        menu.addAction("Delete", lambda: self._delete_group(index))
        menu.exec(QCursor.pos())

    def _build_merge_submenu(self, menu: QMenu, selected: list[LabelDialogue]) -> None:
        merge_menu = menu.addMenu("Merge")
        separators = [(" ", "Space"), (", ", "Comma"), ("\\N", "Newline")]
        for sep, sep_name in separators:
            sub = merge_menu.addMenu(sep_name)
            for perm in itertools.permutations(range(len(selected))):
                order = list(perm)
                display_sep = " | " if sep == "\\N" else sep
                preview = display_sep.join(
                    lb.text[:15] + ("..." if len(lb.text) > 15 else "")
                    for lb in (selected[i] for i in order)
                )
                action = sub.addAction(preview)
                action.triggered.connect(
                    lambda _checked, o=order, s=sep, sl=list(selected): self._on_merge(sl, o, s)
                )

    def _on_merge(self, labels: list[LabelDialogue], order: list[int], separator: str) -> None:
        if not self._ass:
            return
        self._toolbar.hide()
        self._player.clear_selection()
        gi = self._group_index_for_label(labels[0])
        new_label = self._ass.merge_labels(labels, order, separator)
        if gi >= 0:
            group = self._groups[gi]
            for lb in labels:
                if lb in group.labels:
                    group.labels.remove(lb)
            group.labels.append(new_label)
            group.representative_time = _best_representative_time(group.labels)
            self._gallery.refresh_thumbnail(gi)
            self._goto_group(gi)
        self._dirty = True
        _status_msg(self, f"Merged {len(labels)} labels into \"{new_label.text[:30]}\"")

    def _on_empty_context_menu(self, pos: QPointF) -> None:
        menu = QMenu(self)
        menu.addAction("Create Label", lambda: self._create_label_at(pos))

        global_pos = self._player.mapToGlobal(pos.toPoint())
        menu.exec(global_pos)

    def _create_label_at(self, widget_pos: QPointF) -> None:
        if not self._ass:
            return
        ass_x, ass_y = self._player._widget_to_ass(widget_pos.x(), widget_pos.y())
        current_time = self._player._current_time
        # Default: 2 second duration centered on current time
        start_time = max(0.0, current_time - 1.0)
        end_time = current_time + 1.0
        new_label = self._ass.add_label(
            pos_x=ass_x,
            pos_y=ass_y,
            start_time=start_time,
            end_time=end_time,
            text="New Label",
            font_size=self._ass.label_font_size,
        )
        # Add to current group if one exists, otherwise refresh is needed
        gi = self._group_index
        if 0 <= gi < len(self._groups):
            self._groups[gi].labels.append(new_label)
            self._gallery.refresh_thumbnail(gi)
        else:
            # No current group — full rebuild needed (first label ever)
            self._rebuild_gallery()
        self._player.show_time(self._player._current_time)
        self._dirty = True
        _status_msg(self, f"Created label at ({ass_x}, {ass_y})")

    # ── Cleanup ──

    def closeEvent(self, event: QCloseEvent | None) -> None:  # type: ignore[override]
        if event and self._dirty and self._ass:
            reply = QMessageBox.question(
                self,
                "Unsaved Changes",
                "You have unsaved changes. Do you want to save before quitting?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save,
            )
            if reply == QMessageBox.StandardButton.Save:
                self._save_ass()
            elif reply == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
        self._cancel_folder_preload()
        self._cancel_video_setup()
        self._gallery._cancel_loading()
        self._gallery._cancel_single_refresh()
        self._player.shutdown()
        super().closeEvent(event)
