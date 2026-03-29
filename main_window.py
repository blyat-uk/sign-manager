from __future__ import annotations

import glob
import itertools
import re
from dataclasses import dataclass, field
from pathlib import Path

from PyQt6.QtCore import Qt, QPointF, QRectF, QThread, pyqtSignal, QObject, QThreadPool, QRunnable, QSettings, QSize
from PyQt6.QtGui import QColor, QCursor, QKeySequence, QDragEnterEvent, QDropEvent, QShortcut, QCloseEvent, QImage, QFont
from PyQt6.QtWidgets import (
    QMainWindow,
    QToolBar,
    QSplitter,
    QFileDialog,
    QMenu,
    QMessageBox,
    QDockWidget,
    QListWidget,
    QListWidgetItem,
    QLabel,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QStackedWidget,
    QDialog,
    QDialogButtonBox,
    QCheckBox,
    QComboBox,
)

from ass_parser import (
    AssFile, LabelDialogue, _seconds_to_time,
    _FS_TAG_RE, _AN_TAG_RE, _B_TAG_RE, _I_TAG_RE,
    _C_TAG_RE, _3C_TAG_RE, _BORD_TAG_RE,
)
from video_widget import VideoFrameWidget, VideoSetupWorker, detect_fps, detect_duration, _get_video_dimensions, extract_frame_as_image
from gallery_widget import GalleryPanel, LabelGroup, compute_label_groups, _crop_to_labels_image, _best_representative_time
from label_toolbar import LabelToolbar
from mpv_preview import MpvPreviewWidget
from timeline_widget import TimelineWidget

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
    duration: float = 0.0
    thumbnails: dict[int, QImage] = field(default_factory=dict)


class _FilePreloadSignals(QObject):
    """Signals for a single file preload task (QRunnable can't have signals)."""
    file_ready = pyqtSignal(str, object)  # path, PreloadedFileData
    finished = pyqtSignal()


class _FilePreloadTask(QRunnable):
    """Processes a single video file on a QThreadPool thread."""

    def __init__(self, path: str, cancelled: list[bool]):
        super().__init__()
        self.signals = _FilePreloadSignals()
        self._path = path
        self._cancelled = cancelled

    def run(self):
        try:
            if self._cancelled[0]:
                return

            fps = detect_fps(self._path)
            if self._cancelled[0]:
                return
            dims = _get_video_dimensions(self._path)
            if self._cancelled[0]:
                return
            duration = detect_duration(self._path)
            if self._cancelled[0]:
                return
            initial_frame = extract_frame_as_image(self._path, 0)
            if self._cancelled[0]:
                return

            # Find matching .ass file
            video = Path(self._path)
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

            if ass_file and not self._cancelled[0]:
                groups = compute_label_groups(ass_file.labels)
                for i, group in enumerate(groups):
                    if self._cancelled[0]:
                        break
                    img = extract_frame_as_image(self._path, group.representative_time)
                    if img and not img.isNull():
                        cropped = _crop_to_labels_image(
                            img, group,
                            ass_file.play_res_x, ass_file.play_res_y,
                            ass_file.label_font_name, ass_file.label_font_size,
                            ass_file.label_alignment,
                            styles=dict(ass_file.styles),
                        )
                        thumbnails[i] = cropped

            if not self._cancelled[0]:
                data = PreloadedFileData(
                    fps=fps,
                    dims=dims,
                    initial_frame=initial_frame,
                    ass=ass_file,
                    groups=groups,
                    duration=duration,
                    thumbnails=thumbnails,
                )
                self.signals.file_ready.emit(self._path, data)
        finally:
            self.signals.finished.emit()


class FolderPreloadWorker(QObject):
    """Manages parallel file preloading using QThreadPool."""
    file_ready = pyqtSignal(str, object)  # path, PreloadedFileData
    all_done = pyqtSignal()

    _POOL_SIZE = 5

    def __init__(self, file_paths: list[str]):
        super().__init__()
        self._file_paths = file_paths
        self._cancelled: list[bool] = [False]
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(self._POOL_SIZE)
        self._total = len(file_paths)
        self._completed = 0
        self._tasks: list[_FilePreloadTask] = []

    def start(self):
        if not self._file_paths:
            self.all_done.emit()
            return
        for path in self._file_paths:
            task = _FilePreloadTask(path, self._cancelled)
            task.signals.file_ready.connect(self.file_ready)
            task.signals.finished.connect(self._on_task_finished)
            self._tasks.append(task)
            self._pool.start(task)

    def _on_task_finished(self):
        self._completed += 1
        if self._completed >= self._total:
            self.all_done.emit()

    def cancel(self):
        self._cancelled[0] = True
        self._pool.clear()
        self._pool.waitForDone(3000)


class WelcomeWidget(QWidget):
    """Start screen shown when no file is loaded."""

    open_video_clicked = pyqtSignal()
    open_folder_clicked = pyqtSignal()
    recent_directory_clicked = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setStyleSheet("background: #1e1e1e;")

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        layout.addStretch(1)

        title = QLabel("Sub Label Pos")
        title.setStyleSheet("font-size: 28px; color: #ccc; font-weight: bold; background: transparent;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel("ASS Subtitle Label Editor")
        subtitle.setStyleSheet("font-size: 14px; color: #888; background: transparent;")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subtitle)

        layout.addSpacing(24)

        header = QLabel("Recent Directories")
        header.setStyleSheet("font-size: 13px; color: #aaa; background: transparent;")
        header.setAlignment(Qt.AlignmentFlag.AlignLeft)
        header.setFixedWidth(500)
        layout.addWidget(header, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(4)

        self._list = QListWidget()
        self._list.setFixedSize(500, 350)
        self._list.setStyleSheet("""
            QListWidget {
                background: #252525;
                border: 1px solid #555;
                border-radius: 4px;
                outline: none;
            }
            QListWidget::item {
                padding: 8px 10px;
                border-bottom: 1px solid #333;
            }
            QListWidget::item:selected {
                background: #4a9eff;
            }
            QListWidget::item:hover:!selected {
                background: #333;
            }
        """)
        self._list.itemDoubleClicked.connect(self._on_item_activated)
        self._list.installEventFilter(self)
        layout.addWidget(self._list, alignment=Qt.AlignmentFlag.AlignHCenter)

        self._empty_label = QLabel("No recent directories")
        self._empty_label.setStyleSheet("font-size: 12px; color: #666; background: transparent;")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setFixedWidth(500)
        self._empty_label.hide()
        layout.addWidget(self._empty_label, alignment=Qt.AlignmentFlag.AlignHCenter)

        layout.addSpacing(16)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        btn_style = """
            QPushButton {
                background: #3a3a3a;
                color: #ccc;
                border: 1px solid #555;
                border-radius: 4px;
                padding: 8px 24px;
                font-size: 13px;
            }
            QPushButton:hover { background: #505050; }
            QPushButton:pressed { background: #606060; }
        """
        btn_video = QPushButton("Open Video")
        btn_video.setStyleSheet(btn_style)
        btn_video.clicked.connect(self.open_video_clicked)
        btn_row.addWidget(btn_video)

        btn_folder = QPushButton("Open Folder")
        btn_folder.setStyleSheet(btn_style)
        btn_folder.clicked.connect(self.open_folder_clicked)
        btn_row.addWidget(btn_folder)

        layout.addLayout(btn_row)
        layout.addStretch(1)

        self._dirs: list[str] = []

    def set_recent_dirs(self, dirs: list[str]) -> None:
        self._dirs = dirs
        self._list.clear()
        if not dirs:
            self._list.hide()
            self._empty_label.show()
            return
        self._list.show()
        self._empty_label.hide()
        for d in dirs:
            p = Path(d)
            item = QListWidgetItem()
            widget = QWidget()
            widget.setStyleSheet("background: transparent;")
            vbox = QVBoxLayout(widget)
            vbox.setContentsMargins(0, 0, 0, 0)
            vbox.setSpacing(2)
            name_label = QLabel(p.name)
            name_label.setStyleSheet("font-size: 13px; font-weight: bold; color: #ccc; background: transparent;")
            path_label = QLabel(str(p))
            path_label.setStyleSheet("font-size: 11px; color: #888; background: transparent;")
            vbox.addWidget(name_label)
            vbox.addWidget(path_label)
            item.setSizeHint(QSize(480, 44))
            item.setData(Qt.ItemDataRole.UserRole, d)
            self._list.addItem(item)
            self._list.setItemWidget(item, widget)

    def _on_item_activated(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            self.recent_directory_clicked.emit(path)

    def eventFilter(self, obj, event):
        if obj is self._list and event.type() == event.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                item = self._list.currentItem()
                if item:
                    self._on_item_activated(item)
                return True
        return super().eventFilter(obj, event)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Sub Label Pos")
        self.resize(1280, 720)
        self.setAcceptDrops(True)

        self._ass: AssFile | None = None
        self._groups: list[LabelGroup] = []
        self._group_index: int = -1
        self._playback_from_group: bool = True
        self._video_path: str | None = None
        self._ass_path: str | None = None
        self._style_clipboard: dict | None = None
        self.__dirty: bool = False
        self._folder_files: list[str] = []
        self._folder_index: int = -1
        self._suppress_resize: bool = False
        self._playback_mode: bool = False  # True = mpv playing, False = edit mode

        # Async video setup
        self._setup_thread: QThread | None = None
        self._setup_worker: VideoSetupWorker | None = None

        # Folder pre-loading
        self._preloaded: dict[str, PreloadedFileData] = {}
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

        # Layout: splitter with video stack + timeline on top, gallery on bottom
        self._mpv_widget = MpvPreviewWidget()
        # Apply saved mpv settings before GL init (initializeGL is lazy)
        _init_settings = QSettings("SubLabelPos", "SubLabelPos")
        self._mpv_widget._hwdec = _init_settings.value("mpv/hwdec", "auto-safe")
        self._mpv_widget._hq = _init_settings.value("mpv/high_quality", False, type=bool)
        self._player = VideoFrameWidget()
        self._gallery = GalleryPanel()
        self._timeline = TimelineWidget()

        # Video stack: page 0 = mpv (playback), page 1 = editor (QPainter)
        self._video_stack = QStackedWidget()
        self._video_stack.addWidget(self._mpv_widget)   # index 0
        self._video_stack.addWidget(self._player)        # index 1
        self._video_stack.setCurrentIndex(1)  # start in edit mode

        # Container for video stack + timeline (no splitter between them)
        video_container = QWidget()
        vc_layout = QVBoxLayout(video_container)
        vc_layout.setContentsMargins(0, 0, 0, 0)
        vc_layout.setSpacing(0)
        vc_layout.addWidget(self._video_stack, 1)
        vc_layout.addWidget(self._timeline, 0)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(video_container)
        splitter.addWidget(self._gallery)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)

        # Stacked widget: page 0 = welcome, page 1 = editor
        self._welcome = WelcomeWidget()
        self._stacked = QStackedWidget()
        self._stacked.addWidget(self._welcome)  # index 0
        self._stacked.addWidget(splitter)        # index 1
        self.setCentralWidget(self._stacked)

        # Floating toolbar (child of player so it overlays the video)
        self._toolbar = LabelToolbar(self._player)

        # Main toolbar
        self._main_tb = QToolBar("Main")
        self._main_tb.setMovable(False)
        self.addToolBar(self._main_tb)
        self._main_tb.addAction("Open Video", self._open_video)
        self._main_tb.addAction("Open Folder", self._open_folder)
        self._main_tb.addSeparator()
        self._main_tb.addAction("Open ASS", self._open_ass)
        self._main_tb.addAction("Save ASS", self._save_ass)

        # mpv rendering controls
        self._main_tb.addSeparator()
        self._main_tb.addWidget(QLabel("  HW Decode: "))
        self._hwdec_combo = QComboBox()
        self._hwdec_combo.setToolTip("Hardware decoding mode for mpv playback")
        for label, value in [
            ("Auto (safe)", "auto-safe"),
            ("Auto (copy-back)", "auto-copy"),
            ("Software", "no"),
            ("VAAPI", "vaapi"),
            ("VAAPI (copy)", "vaapi-copy"),
            ("NVDEC", "nvdec"),
            ("NVDEC (copy)", "nvdec-copy"),
        ]:
            self._hwdec_combo.addItem(label, value)
        saved_hwdec = _init_settings.value("mpv/hwdec", "auto-safe")
        idx = self._hwdec_combo.findData(saved_hwdec)
        if idx >= 0:
            self._hwdec_combo.setCurrentIndex(idx)
        self._hwdec_combo.currentIndexChanged.connect(self._on_hwdec_changed)
        self._main_tb.addWidget(self._hwdec_combo)

        self._hq_checkbox = QCheckBox("High Quality")
        self._hq_checkbox.setToolTip("Enable spline36 scaling, debanding, and other quality options")
        self._hq_checkbox.setChecked(_init_settings.value("mpv/high_quality", False, type=bool))
        self._hq_checkbox.toggled.connect(self._on_hq_toggled)
        self._main_tb.addWidget(self._hq_checkbox)

        self._main_tb.hide()

        # Shortcuts — frame stepping (arrow keys)
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, self._on_step_forward)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, self._on_step_backward)
        # Gallery navigation (Ctrl+arrow keys)
        QShortcut(QKeySequence("Ctrl+Right"), self, lambda: self._goto_group(self._group_index + 1))
        QShortcut(QKeySequence("Ctrl+Left"), self, lambda: self._goto_group(self._group_index - 1))
        QShortcut(QKeySequence.StandardKey.Save, self, self._save_ass)
        # File navigation (Ctrl+Shift+arrow keys)
        QShortcut(QKeySequence("Ctrl+Shift+Right"), self, self._next_file)
        QShortcut(QKeySequence("Ctrl+Shift+Left"), self, self._prev_file)
        # Delete selected labels
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self, self._on_delete)
        # Bold/Italic shortcuts
        self._bold_shortcut = QShortcut(QKeySequence("Ctrl+B"), self, self._on_bold_shortcut)
        self._italic_shortcut = QShortcut(QKeySequence("Ctrl+I"), self, self._on_italic_shortcut)
        # Play/pause toggle
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, self._toggle_playback)

        # ── Connect signals ──

        # Video widget signals
        self._player.label_moved.connect(self._on_label_moved)
        self._player.label_selected.connect(self._on_label_selected)
        self._player.label_resized.connect(self._on_label_resized)
        self._player.label_rotated.connect(self._on_label_rotated)
        self._player.selection_cleared.connect(self._on_selection_cleared)
        self._player.edit_requested.connect(self._on_edit_requested)
        self._player.text_edited.connect(self._on_text_edited)
        self._player.editing_cancelled.connect(self._on_editing_cancelled)
        self._player.context_menu_requested.connect(self._on_context_menu)
        self._player.empty_context_menu_requested.connect(self._on_empty_context_menu)
        self._player.drag_started.connect(self._on_drag_started)
        self._player.drag_finished.connect(self._on_drag_finished)

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
        self._toolbar.bold_toggled.connect(self._on_bold_toggled)
        self._toolbar.italic_toggled.connect(self._on_italic_toggled)
        self._toolbar.style_changed.connect(self._on_style_changed)
        self._toolbar.primary_colour_changed.connect(self._on_primary_colour_changed)
        self._toolbar.outline_colour_changed.connect(self._on_outline_colour_changed)
        self._toolbar.outline_width_changed.connect(self._on_outline_width_changed)
        self._toolbar.apply_style_clicked.connect(self._on_apply_style)
        self._toolbar.create_style_requested.connect(self._on_create_style)

        # Timeline signals
        self._timeline.time_seeked.connect(self._on_timeline_seeked)
        self._timeline.play_toggled.connect(self._on_play_toggled)
        self._timeline.step_requested.connect(self._on_timeline_step)
        self._timeline.group_clicked.connect(self._goto_group)

        # mpv signals
        self._mpv_widget.time_pos_changed.connect(self._on_mpv_time_pos)
        self._mpv_widget.duration_changed.connect(self._on_mpv_duration)
        self._mpv_widget.pause_changed.connect(self._on_mpv_pause_changed)
        self._mpv_widget.eof_reached.connect(self._on_mpv_eof)

        # Welcome screen signals
        self._welcome.open_video_clicked.connect(self._open_video)
        self._welcome.open_folder_clicked.connect(self._open_folder)
        self._welcome.recent_directory_clicked.connect(self._open_recent_directory)

        # Settings & recent directories
        self._settings = QSettings("SubLabelPos", "SubLabelPos")
        self._load_recent_dirs()

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

    # ── Welcome / recent directories ──

    _MAX_RECENT = 10

    def _switch_to_editor(self) -> None:
        self._stacked.setCurrentIndex(1)
        self._main_tb.show()

    def _load_recent_dirs(self) -> None:
        dirs = self._settings.value("recent_dirs", type=list) or []
        dirs = [d for d in dirs if Path(d).is_dir()]
        self._welcome.set_recent_dirs(dirs)

    def _add_recent_dir(self, directory: str) -> None:
        dirs = self._settings.value("recent_dirs", type=list) or []
        if directory in dirs:
            dirs.remove(directory)
        dirs.insert(0, directory)
        dirs = dirs[:self._MAX_RECENT]
        self._settings.setValue("recent_dirs", dirs)
        self._welcome.set_recent_dirs(dirs)

    def _open_recent_directory(self, folder: str) -> None:
        if not Path(folder).is_dir():
            dirs = self._settings.value("recent_dirs", type=list) or []
            if folder in dirs:
                dirs.remove(folder)
                self._settings.setValue("recent_dirs", dirs)
                self._welcome.set_recent_dirs(dirs)
            QMessageBox.warning(self, "Not Found", f"Directory no longer exists:\n{folder}")
            return
        self._switch_to_editor()
        self._open_folder_path(folder)

    # ── File loading ──

    def _open_video(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Video", "", "Video Files (*.mkv *.mp4 *.avi *.webm);;All (*)"
        )
        if path:
            self._switch_to_editor()
            self._add_recent_dir(str(Path(path).parent))
            self._load_video(path)

    def _load_video(self, path: str, suppress_resize: bool = False) -> None:
        self._cancel_video_setup()
        self._video_path = path
        self._suppress_resize = suppress_resize
        self._player.set_video(path)
        self._mpv_widget.load(path)
        # Ensure we're in edit mode when loading a new video
        self._playback_mode = False
        self._video_stack.setCurrentIndex(1)
        self._timeline.set_playing(False)
        _status_msg(self, "Loading...", 0)

        # Start async video setup
        self._setup_worker = VideoSetupWorker(path)
        self._setup_thread = QThread()
        self._setup_worker.moveToThread(self._setup_thread)
        self._setup_thread.started.connect(self._setup_worker.run)
        self._setup_worker.finished.connect(self._on_video_setup_done)
        self._setup_worker.finished.connect(self._setup_thread.quit)
        self._setup_thread.start()

    def _on_video_setup_done(self, fps: float, dims: object, frame: object, duration: float = 0.0) -> None:
        self._player.set_fps(fps)
        if duration > 0:
            self._timeline.set_duration(duration)

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
        self._switch_to_editor()
        self._open_folder_path(folder)

    def _open_folder_path(self, folder: str) -> None:
        self._cancel_folder_preload()
        def _has_labels(video: Path) -> bool:
            exact = video.with_suffix(".ass")
            if exact.is_file():
                ass_path = exact
            else:
                candidates = sorted(video.parent.glob(f"{glob.escape(video.stem)}.*.ass"))
                if not candidates:
                    return False
                ass_path = candidates[0]
            try:
                ass = AssFile(str(ass_path))
                return bool(ass.labels)
            except Exception:
                return False

        files = [
            str(p) for p in Path(folder).iterdir()
            if p.is_file() and p.suffix.lower() in _VIDEO_EXTS
            and _has_labels(p)
        ]
        files.sort(key=_natural_sort_key)
        if not files:
            QMessageBox.information(self, "No Videos", "No video files with matching .ass subtitle files containing labels found in the selected folder.")
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
        self._add_recent_dir(folder)

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
        self._preload_worker.file_ready.connect(self._on_file_preloaded)
        self._preload_worker.all_done.connect(self._on_preload_done)
        self._preload_worker.start()

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
        self._preload_worker = None
        self._preloaded.clear()

    def _load_video_from_cache(self, path: str, data: PreloadedFileData,
                               suppress_resize: bool) -> None:
        """Load a video file using pre-loaded data (near-instant)."""
        self._cancel_video_setup()
        self._video_path = path
        self._player.set_video(path)
        self._player.set_fps(data.fps)
        self._mpv_widget.load(path)
        # Ensure edit mode
        self._playback_mode = False
        self._video_stack.setCurrentIndex(1)
        self._timeline.set_playing(False)
        if data.duration > 0:
            self._timeline.set_duration(data.duration)

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
            self._ass_path = data.ass.path
            self._dirty = False
            self._player.set_ass(self._ass)
            self._mpv_widget.load_subtitles(data.ass.path)
            self._toolbar.hide()
            self._gallery.set_data_preloaded(
                path, data.ass, data.groups, data.thumbnails,
            )
            self._groups = self._gallery.groups
            self._timeline.set_groups(self._groups)
            if self._groups:
                self._goto_group(0)
        else:
            self._ass = None
            self._ass_path = None
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
        self._ass_path = path
        self._dirty = False
        self._player.set_ass(self._ass)
        self._mpv_widget.load_subtitles(path)
        self._toolbar.hide()
        _status_msg(self, f"Loaded {len(self._ass.labels)} labels from {path}")

        if self._video_path:
            self._rebuild_gallery()
            self._timeline.set_groups(self._groups)
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
        self._mpv_widget.reload_subtitles()
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
            self._timeline.set_groups(self._groups)

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
        self._playback_from_group = True
        group = self._groups[index]
        # If in playback mode, pause and enter edit mode
        if self._playback_mode:
            self._mpv_widget.pause()
            self._playback_mode = False
            self._video_stack.setCurrentIndex(1)
            self._timeline.set_playing(False)
        self._player.show_time(group.representative_time)
        self._player.prefetch_around(group.representative_time)
        self._gallery.select_group(index)
        self._timeline.set_time(group.representative_time)

    def _on_group_selected(self, index: int) -> None:
        self._group_index = index
        self._playback_from_group = True
        if 0 <= index < len(self._groups):
            t = self._groups[index].representative_time
            # If in playback mode, switch to edit mode
            if self._playback_mode:
                self._mpv_widget.pause()
                self._playback_mode = False
                self._video_stack.setCurrentIndex(1)
                self._timeline.set_playing(False)
            self._player.show_time(t)
            self._player.prefetch_around(t)
            self._timeline.set_time(t)

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
                        self._switch_to_editor()
                        self._add_recent_dir(str(Path(path).parent))
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

    def _on_label_resized(self, label, new_fs):
        self._dirty = True
        self._toolbar._font_size = new_fs
        self._toolbar._size_label.setText(str(new_fs))

    def _on_label_rotated(self, label, rotation):
        self._dirty = True

    def _on_label_selected(self, label: LabelDialogue) -> None:
        selected = self._player.selected_labels()
        multi = len(selected) > 1
        if label.font_size is not None:
            font_size = label.font_size
        elif self._ass:
            style = self._ass.styles.get(label.style_name)
            font_size = style.font_size if style else self._ass.label_font_size
        else:
            font_size = 36
        if multi:
            alignments = {lb.alignment for lb in selected}
            effective_alignment = alignments.pop() if len(alignments) == 1 else None
        else:
            effective_alignment = label.alignment

        # Determine bold/italic state
        if self._ass:
            style = self._ass.styles.get(label.style_name)
            default_bold = style.bold if style else self._ass.label_bold
            default_italic = style.italic if style else self._ass.label_italic
        else:
            default_bold = False
            default_italic = False
        bold = label.bold if label.bold is not None else default_bold
        italic = label.italic if label.italic is not None else default_italic

        # Determine colour/outline state
        if self._ass:
            style = self._ass.styles.get(label.style_name)
            primary_colour = label.primary_colour if label.primary_colour is not None else (
                style.primary_colour if style else "&H00FFFFFF&"
            )
            outline_colour = label.outline_colour if label.outline_colour is not None else (
                style.outline_colour if style else "&H00000000&"
            )
            outline_width = label.outline_width if label.outline_width is not None else (
                style.outline_width if style else 2.0
            )
        else:
            primary_colour = "&H00FFFFFF&"
            outline_colour = "&H00000000&"
            outline_width = 2.0

        available_styles = list(self._ass.styles.keys()) if self._ass else []

        self._toolbar.set_multi_mode(multi)
        self._toolbar.show_for_label(
            font_size, effective_alignment,
            bold=bold, italic=italic,
            style_name=label.style_name,
            available_styles=available_styles,
            primary_colour=primary_colour,
            outline_colour=outline_colour,
            outline_width=outline_width,
        )
        self._update_toolbar_position()

    def _on_selection_cleared(self) -> None:
        self._toolbar.hide()
        self._bold_shortcut.setEnabled(True)
        self._italic_shortcut.setEnabled(True)

    def _on_drag_started(self) -> None:
        self._toolbar.hide()

    def _on_drag_finished(self) -> None:
        self._refresh_toolbar_for_selection()

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

    def _on_sync_times(self, labels: list) -> None:
        if not self._ass or len(labels) < 2:
            return
        start = min(lb.start_time for lb in labels)
        end = max(lb.end_time for lb in labels)
        for lb in labels:
            self._ass.set_label_times(lb, start, end)
        self._dirty = True
        self._player.show_time(self._player._current_time)
        _status_msg(self, f"Synced {len(labels)} labels to {_seconds_to_time(start)} \u2192 {_seconds_to_time(end)}")

    def _on_delete(self) -> None:
        if not self._ass:
            return
        selected = self._player.selected_labels()
        if not selected:
            # No editor labels selected — try deleting selected gallery group
            gi = self._gallery._selected_index
            if gi >= 0:
                self._delete_group(gi)
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

    def _on_copy_style(self, selected_attrs: set | None = None) -> None:
        selected = self._player.selected_labels()
        if not selected:
            return
        if selected_attrs is None:
            selected_attrs = {"font_size", "alignment", "bold", "italic", "style",
                              "primary_colour", "outline_colour", "outline_width", "rotation",
                              "position"}
        label = selected[0]
        if label.font_size is not None:
            font_size = label.font_size
        elif self._ass:
            style = self._ass.styles.get(label.style_name)
            font_size = style.font_size if style else self._ass.label_font_size
        else:
            font_size = 36
        self._style_clipboard = {
            "font_size": font_size if "font_size" in selected_attrs else None,
            "alignment": label.alignment if "alignment" in selected_attrs else None,
            "bold": label.bold if "bold" in selected_attrs else None,
            "italic": label.italic if "italic" in selected_attrs else None,
            "style": label.style_name if "style" in selected_attrs else None,
            "primary_colour": label.primary_colour if "primary_colour" in selected_attrs else None,
            "outline_colour": label.outline_colour if "outline_colour" in selected_attrs else None,
            "outline_width": label.outline_width if "outline_width" in selected_attrs else None,
            "rotation": label.rotation if "rotation" in selected_attrs else None,
            "position": (label.pos_x, label.pos_y) if "position" in selected_attrs else None,
        }
        copied = [k for k in selected_attrs if self._style_clipboard.get(k) is not None]
        _status_msg(self, f"Copied: {', '.join(copied) if copied else 'nothing'}")

    def _on_paste_style(self) -> None:
        if not self._ass or self._style_clipboard is None:
            return
        clip = self._style_clipboard
        font_size = clip["font_size"]
        alignment = clip["alignment"]
        bold = clip["bold"]
        italic = clip["italic"]
        style_name = clip["style"]
        primary_colour = clip["primary_colour"]
        outline_colour = clip["outline_colour"]
        outline_width = clip["outline_width"]
        rotation = clip["rotation"]
        position = clip["position"]
        for label in self._player.selected_labels():
            if bold is not None:
                self._ass.set_label_bold(label, bold)
            if italic is not None:
                self._ass.set_label_italic(label, italic)
            if primary_colour is not None:
                self._ass.set_label_primary_colour(label, primary_colour)
            if outline_colour is not None:
                self._ass.set_label_outline_colour(label, outline_colour)
            if outline_width is not None:
                self._ass.set_label_outline_width(label, outline_width)
            if rotation is not None:
                self._ass.set_label_rotation(label, rotation)
            if style_name and style_name in self._ass.styles:
                self._ass.set_label_style(label, style_name)
            if font_size is not None:
                self._ass.set_label_font_size(label, font_size)
            if position is not None:
                self._ass.set_label_position(label, position[0], position[1])
            if alignment is not None:
                font = self._player._font_for_label(label)
                rect = self._player._compute_rect(label, font)
                new_anchor = _anchor_for_alignment(rect, alignment)
                new_x, new_y = self._player._widget_to_ass(new_anchor.x(), new_anchor.y())
                self._ass.set_label_alignment(label, alignment)
                self._ass.set_label_position(label, new_x, new_y)
        self._dirty = True
        self._player._font_corrections.clear()
        self._player.update()
        self._refresh_toolbar_for_selection()
        pasted = [k for k, v in clip.items() if v is not None]
        _status_msg(self, f"Pasted: {', '.join(pasted) if pasted else 'nothing'}")

    def _on_bold_toggled(self, bold: bool) -> None:
        if not self._ass:
            return
        for label in self._player.selected_labels():
            self._ass.set_label_bold(label, bold)
        self._dirty = True
        self._player.update()
        self._update_toolbar_position()

    def _on_italic_toggled(self, italic: bool) -> None:
        if not self._ass:
            return
        for label in self._player.selected_labels():
            self._ass.set_label_italic(label, italic)
        self._dirty = True
        self._player.update()
        self._update_toolbar_position()

    def _on_style_changed(self, style_name: str) -> None:
        if not self._ass or style_name not in self._ass.styles:
            return
        for label in self._player.selected_labels():
            self._ass.set_label_style(label, style_name)
        self._dirty = True
        self._player._font_corrections.clear()
        self._player.update()
        self._refresh_toolbar_for_selection()

    def _on_primary_colour_changed(self, colour: str) -> None:
        if not self._ass:
            return
        for label in self._player.selected_labels():
            self._ass.set_label_primary_colour(label, colour)
        self._dirty = True
        self._player.update()

    def _on_outline_colour_changed(self, colour: str) -> None:
        if not self._ass:
            return
        for label in self._player.selected_labels():
            self._ass.set_label_outline_colour(label, colour)
        self._dirty = True
        self._player.update()

    def _on_outline_width_changed(self, width: float) -> None:
        if not self._ass:
            return
        for label in self._player.selected_labels():
            self._ass.set_label_outline_width(label, width)
        self._dirty = True
        self._player.update()

    def _on_create_style(self, name: str) -> None:
        if not self._ass:
            return
        if name in self._ass.styles:
            QMessageBox.warning(self, "Duplicate Style", f"A style named '{name}' already exists.")
            return
        selected = self._player.selected_labels()
        # Get template from current label's style
        template = None
        if selected:
            template = self._ass.styles.get(selected[0].style_name)
        elif self._ass.styles:
            template = next(iter(self._ass.styles.values()))
        new_style = self._ass.add_style(name, template)
        # Apply label's inline overrides into the new style
        if selected:
            label = selected[0]
            if label.font_size is not None:
                self._ass.update_style_field(name, 2, str(label.font_size))
            if label.bold is not None:
                self._ass.update_style_field(name, 7, "1" if label.bold else "0")
            if label.italic is not None:
                self._ass.update_style_field(name, 8, "1" if label.italic else "0")
            if label.alignment is not None:
                self._ass.update_style_field(name, 18, str(label.alignment))
            if label.primary_colour is not None:
                self._ass.update_style_field(name, 3, label.primary_colour)
            if label.outline_colour is not None:
                self._ass.update_style_field(name, 5, label.outline_colour)
            if label.outline_width is not None:
                self._ass.update_style_field(name, 16, f"{label.outline_width:g}")
            # Remove inline tags that were promoted
            for tag_re, attr in [
                (_FS_TAG_RE, "font_size"), (_AN_TAG_RE, "alignment"),
                (_B_TAG_RE, "bold"), (_I_TAG_RE, "italic"),
                (_C_TAG_RE, "primary_colour"), (_3C_TAG_RE, "outline_colour"),
                (_BORD_TAG_RE, "outline_width"),
            ]:
                if getattr(label, attr) is not None:
                    self._ass.remove_inline_tag(label, tag_re, attr)
            # Assign label to the new style
            self._ass.set_label_style(label, name)
        self._dirty = True
        self._player.update()
        self._refresh_toolbar_for_selection()
        _status_msg(self, f"Created style '{name}'")

    def _on_apply_style(self) -> None:
        if not self._ass:
            return
        selected = self._player.selected_labels()
        if not selected:
            return
        label = selected[0]
        style_name = label.style_name

        # Build list of properties that have inline overrides
        props = [
            ("Font Size", _FS_TAG_RE, "font_size", 2, label.font_size),
            ("Primary Colour", _C_TAG_RE, "primary_colour", 3, label.primary_colour),
            ("Outline Colour", _3C_TAG_RE, "outline_colour", 5, label.outline_colour),
            ("Bold", _B_TAG_RE, "bold", 7, label.bold),
            ("Italic", _I_TAG_RE, "italic", 8, label.italic),
            ("Outline Width", _BORD_TAG_RE, "outline_width", 16, label.outline_width),
            ("Alignment", _AN_TAG_RE, "alignment", 18, label.alignment),
        ]

        dialog = QDialog(self)
        dialog.setWindowTitle("Apply to Style")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(f"Promote inline overrides to style '{style_name}':"))

        checkboxes: list[tuple[QCheckBox, str, object, int, str]] = []
        for display_name, tag_re, attr, field_idx, value in props:
            cb = QCheckBox(display_name)
            has_override = value is not None
            cb.setEnabled(has_override)
            cb.setChecked(has_override)
            layout.addWidget(cb)
            checkboxes.append((cb, attr, tag_re, field_idx, attr))

        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(dialog.accept)
        btn_box.rejected.connect(dialog.reject)
        layout.addWidget(btn_box)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        for cb, attr, tag_re, field_idx, attr_name in checkboxes:
            if not cb.isChecked():
                continue
            value = getattr(label, attr)
            if value is None:
                continue
            # Convert value to string for style field
            if isinstance(value, bool):
                field_value = "1" if value else "0"
            elif isinstance(value, float):
                field_value = f"{value:g}"
            else:
                field_value = str(value)
            self._ass.update_style_field(style_name, field_idx, field_value)
            self._ass.remove_inline_tag_from_all(style_name, tag_re, attr_name)

        self._dirty = True
        self._player.update()
        self._refresh_toolbar_for_selection()
        _status_msg(self, f"Applied overrides to style '{style_name}'")

    def _refresh_toolbar_for_selection(self) -> None:
        """Re-read the selected label state and update the toolbar."""
        selected = self._player.selected_labels()
        if selected:
            self._on_label_selected(selected[0])

    def _on_bold_shortcut(self) -> None:
        # If inline editor is active, let QTextEdit handle Ctrl+B
        if self._player._text_edit is not None:
            return
        selected = self._player.selected_labels()
        if not selected or not self._ass:
            return
        # Toggle: if any selected label is bold, make all non-bold; otherwise make all bold
        def _is_bold(lb: LabelDialogue) -> bool:
            if lb.bold is not None:
                return lb.bold
            st = self._ass.styles.get(lb.style_name) if self._ass else None
            return st.bold if st else False

        any_bold = any(_is_bold(lb) for lb in selected)
        new_bold = not any_bold
        for lb in selected:
            self._ass.set_label_bold(lb, new_bold)
        self._dirty = True
        self._toolbar._bold_btn.blockSignals(True)
        self._toolbar._bold_btn.setChecked(new_bold)
        self._toolbar._bold_btn.blockSignals(False)
        self._player.update()
        self._update_toolbar_position()

    def _on_italic_shortcut(self) -> None:
        if self._player._text_edit is not None:
            return
        selected = self._player.selected_labels()
        if not selected or not self._ass:
            return

        def _is_italic(lb: LabelDialogue) -> bool:
            if lb.italic is not None:
                return lb.italic
            st = self._ass.styles.get(lb.style_name) if self._ass else None
            return st.italic if st else False

        any_italic = any(_is_italic(lb) for lb in selected)
        new_italic = not any_italic
        for lb in selected:
            self._ass.set_label_italic(lb, new_italic)
        self._dirty = True
        self._toolbar._italic_btn.blockSignals(True)
        self._toolbar._italic_btn.setChecked(new_italic)
        self._toolbar._italic_btn.blockSignals(False)
        self._player.update()
        self._update_toolbar_position()

    # ── Inline text editing ──

    def _on_edit_requested(self, label: LabelDialogue) -> None:
        self._toolbar.hide()
        self._bold_shortcut.setEnabled(False)
        self._italic_shortcut.setEnabled(False)
        self._player.start_editing(label)

    def _on_editing_cancelled(self) -> None:
        self._bold_shortcut.setEnabled(True)
        self._italic_shortcut.setEnabled(True)

    def _on_text_edited(self, label: LabelDialogue, rich_text: str) -> None:
        self._bold_shortcut.setEnabled(True)
        self._italic_shortcut.setEnabled(True)
        if not self._ass:
            return
        self._ass.set_label_rich_text(label, rich_text)
        self._dirty = True
        self._player.show_time(self._player._current_time)
        # Update just the text on the affected thumbnail
        gi = self._group_index_for_label(label)
        if gi >= 0 and gi < len(self._gallery._thumbnails):
            texts = [lb.text for lb in self._groups[gi].labels]
            self._gallery._thumbnails[gi].update_texts(texts)
        _status_msg(self, f"Updated text to \"{label.text}\"")

    # ── Context menus ──

    def _on_context_menu(self, pos: QPointF) -> None:
        menu = QMenu(self)
        selected = self._player.selected_labels()

        menu.addAction("Duplicate", self._on_duplicate)
        menu.addAction("Delete", self._on_delete)
        menu.addSeparator()

        if len(selected) >= 2:
            menu.addAction("Sync Times", lambda: self._on_sync_times(selected))

        if 2 <= len(selected) <= 3:
            self._build_merge_submenu(menu, selected)

        if len(selected) == 1:
            menu.addAction("Edit Text", lambda: self._on_edit_requested(selected[0]))
            menu.addAction("Copy Style", lambda: self._on_copy_style())
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
        # Add to current group only if time ranges overlap, otherwise rebuild
        gi = self._group_index
        added_to_current = False
        if 0 <= gi < len(self._groups):
            group = self._groups[gi]
            if group.labels:
                group_start = min(lb.start_time for lb in group.labels)
                group_end = max(lb.end_time for lb in group.labels)
                if start_time <= group_end and end_time >= group_start:
                    group.labels.append(new_label)
                    group.representative_time = _best_representative_time(group.labels)
                    self._gallery.refresh_thumbnail(gi)
                    added_to_current = True
        if not added_to_current:
            self._rebuild_gallery()
            new_gi = self._group_index_for_label(new_label)
            if new_gi >= 0:
                self._goto_group(new_gi)
        self._player.show_time(self._player._current_time)
        self._dirty = True
        _status_msg(self, f"Created label at ({ass_x}, {ass_y})")

    # ── Playback / edit mode switching ──

    def _playback_start_time(self) -> float:
        """Return the time to start playback from.

        If the user navigated via gallery/group selection, start from the
        earliest label in the group.  If the user manually seeked or stepped
        frames, start from their current position.
        """
        if self._playback_from_group and 0 <= self._group_index < len(self._groups):
            group = self._groups[self._group_index]
            if group.labels:
                return min(lb.start_time for lb in group.labels)
        return self._player._current_time

    def _enter_playback_mode(self) -> None:
        """Switch to mpv playback mode."""
        if self._playback_mode:
            return
        if not self._video_path:
            return
        self._playback_mode = True
        self._toolbar.hide()
        # Switch to mpv widget (triggers initializeGL on first use)
        self._video_stack.setCurrentIndex(0)

        if self._mpv_widget.is_file_loaded:
            start = self._playback_start_time()
            self._mpv_widget.seek_absolute(start)
            self._mpv_widget.play()
        else:
            # File not yet loaded — wait for file_loaded signal
            self._mpv_widget.file_loaded.connect(
                self._on_mpv_file_loaded_for_playback,
                Qt.ConnectionType.SingleShotConnection,
            )
        self._timeline.set_playing(True)

    def _on_mpv_file_loaded_for_playback(self) -> None:
        """Called when mpv finishes loading a file and we want to start playback."""
        if self._playback_mode:
            start = self._playback_start_time()
            self._mpv_widget.seek_absolute(start)
            self._mpv_widget.play()
            # Also load subtitles if we have them
            if self._ass_path:
                self._mpv_widget.load_subtitles(self._ass_path)

    def _enter_edit_mode(self, capture: bool = True) -> None:
        """Switch to QPainter edit mode, optionally capturing mpv's current frame."""
        if not self._playback_mode:
            return
        self._mpv_widget.pause()
        self._playback_mode = False

        time_pos = self._mpv_widget.time_pos

        # Set _current_time BEFORE show_frame_from_image so the captured frame
        # is cached under the mpv pause time, not the previous representative time.
        self._player._current_time = time_pos

        if capture:
            frame_data = self._mpv_widget.capture_frame()
            if frame_data:
                rgb_bytes, w, h = frame_data
                img = QImage(rgb_bytes, w, h, w * 3, QImage.Format.Format_RGB888)
                # QImage doesn't copy the data, so .copy() ensures it's owned
                img = img.copy()
                self._player.show_frame_from_image(img)
        self._player._update_visible_labels(time_pos)
        self._player._update_scaled_pixmap()
        self._player.update()

        self._video_stack.setCurrentIndex(1)
        self._timeline.set_playing(False)
        self._timeline.set_time(time_pos)

    def _toggle_playback(self) -> None:
        """Toggle between playback and edit modes (Space bar)."""
        # Don't toggle if inline text editor is active
        if self._player._text_edit is not None:
            return
        if not self._video_path:
            return
        if self._playback_mode:
            self._enter_edit_mode()
        else:
            self._enter_playback_mode()

    def _on_play_toggled(self, playing: bool) -> None:
        """Handle play/pause button from timeline widget."""
        if playing:
            self._enter_playback_mode()
        else:
            self._enter_edit_mode()

    def _on_timeline_seeked(self, seconds: float) -> None:
        """Handle scrubber drag from timeline."""
        self._playback_from_group = False
        if self._playback_mode:
            # Pause and enter edit mode at the seeked position
            self._mpv_widget.pause()
            self._playback_mode = False
            self._mpv_widget.seek_absolute(seconds)
            self._video_stack.setCurrentIndex(1)
            self._timeline.set_playing(False)
            # Use ffmpeg to extract the frame in edit mode
            self._player.show_time(seconds)
        else:
            self._player.show_time(seconds)

    def _on_timeline_step(self, delta: int) -> None:
        """Handle frame step buttons from timeline."""
        self._playback_from_group = False
        if self._playback_mode:
            self._enter_edit_mode()
        if delta > 0:
            self._player.step_frame(1)
        else:
            self._player.step_frame(-1)
        self._timeline.set_time(self._player._current_time)

    def _on_step_forward(self) -> None:
        """Arrow right — frame step in current mode."""
        self._playback_from_group = False
        if self._playback_mode:
            self._mpv_widget.frame_step(forward=True)
        else:
            self._player.step_frame(1)
            self._timeline.set_time(self._player._current_time)

    def _on_step_backward(self) -> None:
        """Arrow left — frame step in current mode."""
        self._playback_from_group = False
        if self._playback_mode:
            self._mpv_widget.frame_step(forward=False)
        else:
            self._player.step_frame(-1)
            self._timeline.set_time(self._player._current_time)

    def _on_mpv_time_pos(self, seconds: float) -> None:
        """Sync timeline with mpv playback position."""
        if self._playback_mode:
            self._timeline.set_time(seconds)

    def _on_mpv_duration(self, duration: float) -> None:
        """Update timeline when mpv reports video duration."""
        self._timeline.set_duration(duration)

    def _on_mpv_pause_changed(self, paused: bool) -> None:
        """Handle mpv pause state changes."""
        self._timeline.set_playing(not paused)

    def _on_mpv_eof(self) -> None:
        """Handle end-of-file — switch to edit mode."""
        if self._playback_mode:
            self._enter_edit_mode(capture=False)

    # ── mpv rendering settings ──

    def _on_hwdec_changed(self, index: int) -> None:
        value = self._hwdec_combo.currentData()
        self._mpv_widget.set_hwdec(value)
        self._settings.setValue("mpv/hwdec", value)

    def _on_hq_toggled(self, checked: bool) -> None:
        self._mpv_widget.set_high_quality(checked)
        self._settings.setValue("mpv/high_quality", checked)

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
        self._mpv_widget.shutdown()
        self._player.shutdown()
        super().closeEvent(event)
