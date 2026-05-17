from __future__ import annotations

import glob
import itertools
import re
from dataclasses import dataclass, field
from pathlib import Path

from PyQt6.QtCore import Qt, QPointF, QRectF, QThread, pyqtSignal, QObject, QThreadPool, QRunnable, QSettings, QSize
from PyQt6.QtGui import QAction, QColor, QCursor, QKeySequence, QDragEnterEvent, QDropEvent, QShortcut, QCloseEvent, QImage, QFont
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
    QStyle,
    QDialog,
    QDialogButtonBox,
    QCheckBox,
    QComboBox,
)

from sub_label_pos.model.ass_file import (
    AssFile, LabelDialogue, _seconds_to_time,
    _FS_TAG_RE, _AN_TAG_RE, _B_TAG_RE, _I_TAG_RE,
    _C_TAG_RE, _3C_TAG_RE, _BORD_TAG_RE,
)
from sub_label_pos.model.groups import DerivedGroupModel
from sub_label_pos.model.label_store import LabelStore
from sub_label_pos.services.exceptions import VideoServiceError
from sub_label_pos.services.ffmpeg_service import FFmpegVideoService
from sub_label_pos.services.frame_cache import FrameCache
from sub_label_pos.services.frame_request_queue import FrameRequestQueue
from sub_label_pos.services.mpv_service import MpvPreviewWidget
from sub_label_pos.services.preload import FilePreloadTask, PreloadResult, PreloadSignals
from sub_label_pos.services.video_service import CachedVideoService, VideoService
from sub_label_pos.model.types import StylePatch
from sub_label_pos import shortcuts
from sub_label_pos.ui.controllers.file_loader import FileLoader, VideoFilePair
from sub_label_pos.ui.controllers.label_edit_controller import LabelEditController
from sub_label_pos.ui.controllers.playback_orchestrator import PlaybackOrchestrator
from sub_label_pos.ui.video_widget import VideoFrameWidget
from sub_label_pos.ui.gallery_widget import GalleryPanel, LabelGroup, compute_label_groups, _crop_to_labels_image, _best_representative_time
from sub_label_pos.ui.label_toolbar import LabelToolbar
from sub_label_pos.ui.timeline_widget import TimelineWidget

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


class _RichPreloadSignals(QObject):
    """Signals for a single rich-preload task (QRunnable can't host signals)."""
    file_ready = pyqtSignal(str, object)  # path, PreloadedFileData
    finished = pyqtSignal()


class _RichFilePreloadTask(QRunnable):
    """Composite preload task: metadata + first frame (via ``FilePreloadTask``),
    then matching ASS lookup, label-group computation, and per-group thumbnails.

    All work runs on a QThreadPool thread; only Qt signal emission crosses
    into the UI thread. Per-field failures are non-fatal — missing data
    surfaces as ``None``/empty on the downstream ``PreloadedFileData``.
    """

    def __init__(self, path: str, video_service: VideoService) -> None:
        super().__init__()
        self.signals = _RichPreloadSignals()
        self._path = path
        self._svc = video_service
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            if self._cancelled:
                return

            video_path = Path(self._path)

            # --- Metadata + first frame via the extracted FilePreloadTask ---
            captured: list[PreloadResult] = []
            inner_signals = PreloadSignals()
            inner_signals.completed.connect(captured.append)
            FilePreloadTask(video_path, self._svc, inner_signals).run()
            if self._cancelled or not captured:
                return
            pr = captured[0]

            # Fall back to safe defaults so downstream consumers don't have to
            # special-case missing metadata (preserves pre-H7 behaviour).
            fps = pr.fps if pr.fps is not None else 24.0
            dims = pr.dimensions
            duration = pr.duration if pr.duration is not None else 0.0
            initial_frame = pr.first_frame

            # --- Locate a matching .ass sidecar ---
            ass_file: AssFile | None = None
            exact = video_path.with_suffix(".ass")
            if exact.is_file():
                ass_file = AssFile(str(exact))
            else:
                candidates = sorted(
                    video_path.parent.glob(f"{glob.escape(video_path.stem)}.*.ass")
                )
                if candidates:
                    ass_file = AssFile(str(candidates[0]))

            # --- Label groups + per-group thumbnails ---
            groups: list[LabelGroup] = []
            thumbnails: dict[int, QImage] = {}

            if ass_file and not self._cancelled:
                groups = compute_label_groups(ass_file.labels)
                for i, group in enumerate(groups):
                    if self._cancelled:
                        break
                    try:
                        img = self._svc.get_frame(video_path, group.representative_time)
                    except VideoServiceError:
                        continue
                    if img and not img.isNull():
                        cropped = _crop_to_labels_image(
                            img, group,
                            ass_file.play_res_x, ass_file.play_res_y,
                            ass_file.label_font_name, ass_file.label_font_size,
                            ass_file.label_alignment,
                            styles=dict(ass_file.styles),
                        )
                        thumbnails[i] = cropped

            if not self._cancelled:
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

    def __init__(self, file_paths: list[str], video_service: VideoService):
        super().__init__()
        self._file_paths = file_paths
        self._video_service = video_service
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(self._POOL_SIZE)
        self._total = len(file_paths)
        self._completed = 0
        self._tasks: list[_RichFilePreloadTask] = []

    def start(self):
        if not self._file_paths:
            self.all_done.emit()
            return
        for path in self._file_paths:
            task = _RichFilePreloadTask(path, self._video_service)
            task.signals.file_ready.connect(self.file_ready)
            task.signals.finished.connect(self._on_task_finished)
            self._tasks.append(task)
            self._pool.start(task)

    def _on_task_finished(self):
        self._completed += 1
        if self._completed >= self._total:
            self.all_done.emit()

    def cancel(self):
        for task in self._tasks:
            task.cancel()
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

        # Video service stack (frame extraction + LRU cache + async queue)
        self._video_service: VideoService = CachedVideoService(
            FFmpegVideoService(), FrameCache(max_size=64),
        )
        self._frame_queue = FrameRequestQueue(self._video_service, workers=2)

        # FileLoader handles the "load one video + its sidecar ASS" primitive
        # on a worker thread. MainWindow only orchestrates higher-level
        # concerns (preload cache, mode switching, recent dirs) and reacts
        # to the loaded/load_failed signals.
        self._file_loader = FileLoader(self._video_service)
        self._file_loader.loaded.connect(self._on_file_loaded)
        self._file_loader.load_failed.connect(self._on_file_load_failed)

        # Label store + derived groups (gallery and other store-aware widgets
        # subscribe to these). MainWindow still keeps its own ``self._ass`` /
        # ``self._groups`` for legacy paths — those will be retired as
        # mutations move into store.apply() (tasks J3/J4/K1/L1).
        self._store = LabelStore()
        self._groups_model = DerivedGroupModel(self._store)
        self._edit = LabelEditController(self._store)

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
        # Edit/playback mode switching + mpv↔timeline sync lives in the
        # orchestrator now (task K3). MainWindow still constructs the mpv
        # widget and video stack — the orchestrator only owns the mode
        # transitions and their side-effects.
        self._playback = PlaybackOrchestrator()

        # Suppress-resize state lives here because FileLoader's loaded signal
        # is fire-and-forget; the handler reads this flag to decide whether
        # to centre/resize the window for the new video.

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
        self._player = VideoFrameWidget(self._store, self._video_service)
        self._gallery = GalleryPanel(
            store=self._store,
            groups=self._groups_model,
            video_service=self._video_service,
        )
        self._timeline = TimelineWidget(groups=self._groups_model)

        # Video stack: page 0 = mpv (playback), page 1 = editor (QPainter)
        self._video_stack = QStackedWidget()
        self._video_stack.addWidget(self._mpv_widget)   # index 0
        self._video_stack.addWidget(self._player)        # index 1
        self._video_stack.setCurrentIndex(1)  # start in edit mode

        # Hand the orchestrator references to the widgets it needs to drive.
        self._playback.attach_mpv_widget(self._mpv_widget)
        self._playback.attach_editor_widget(self._player)
        self._playback.attach_timeline_widget(self._timeline)
        self._playback.attach_video_stack(self._video_stack)
        self._playback.set_start_time_provider(self._playback_start_time)
        self._playback.mode_changed.connect(self._on_playback_mode_changed)

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

        # Floating toolbar (child of player so it overlays the video).
        # The toolbar derives its display state from store signals; MainWindow
        # only routes the user-driven button actions.
        self._toolbar = LabelToolbar(self._store, parent=self._player)

        # Main toolbar
        self._main_tb = QToolBar("Main")
        self._main_tb.setMovable(False)
        self.addToolBar(self._main_tb)
        self._main_tb.addAction("Open Video", self._open_video)
        self._main_tb.addAction("Open Folder", self._open_folder)
        self._main_tb.addSeparator()
        self._main_tb.addAction("Open ASS", self._open_ass)
        self._main_tb.addAction("Save ASS", self._save_ass)

        # Undo / redo actions (enabled state mirrors the store's UndoStack).
        # Shortcut keys are not bound to the QAction here — global QShortcut
        # objects below own the key bindings to avoid Qt's "ambiguous shortcut
        # overload" warning. The shortcut text is shown in the tooltip only.
        self._main_tb.addSeparator()
        undo_action = QAction("Undo", self)
        undo_action.setToolTip("Undo (" + shortcuts.UNDO.toString() + ")")
        undo_action.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowBack))
        undo_action.setEnabled(False)
        undo_action.triggered.connect(self._edit.undo)

        redo_action = QAction("Redo", self)
        redo_action.setToolTip("Redo (" + shortcuts.REDO.toString() + ")")
        redo_action.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowForward))
        redo_action.setEnabled(False)
        redo_action.triggered.connect(self._edit.redo)

        self._undo_action = undo_action
        self._redo_action = redo_action
        self._main_tb.addAction(undo_action)
        self._main_tb.addAction(redo_action)

        self._store.undo_stack.can_undo_changed.connect(undo_action.setEnabled)
        self._store.undo_stack.can_redo_changed.connect(redo_action.setEnabled)

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
        QShortcut(shortcuts.NEXT_FRAME, self, self._on_step_forward)
        QShortcut(shortcuts.PREV_FRAME, self, self._on_step_backward)
        # Gallery navigation (Ctrl+arrow keys)
        QShortcut(shortcuts.NEXT_GROUP, self, lambda: self._goto_group(self._group_index + 1))
        QShortcut(shortcuts.PREV_GROUP, self, lambda: self._goto_group(self._group_index - 1))
        QShortcut(shortcuts.SAVE, self, self._save_ass)
        # File navigation (Ctrl+Shift+arrow keys)
        QShortcut(shortcuts.NEXT_FILE, self, self._next_file)
        QShortcut(shortcuts.PREV_FILE, self, self._prev_file)
        # Delete selected labels
        QShortcut(shortcuts.DELETE_SELECTED, self, self._on_delete)
        # Bold/Italic shortcuts
        self._bold_shortcut = QShortcut(shortcuts.BOLD, self, self._on_bold_shortcut)
        self._italic_shortcut = QShortcut(shortcuts.ITALIC, self, self._on_italic_shortcut)
        # Play/pause toggle
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, self._toggle_playback)
        # Undo / redo — multiple bindings so Ctrl+Z, Ctrl+Y, and Ctrl+Shift+Z
        # all work regardless of platform defaults.
        QShortcut(shortcuts.UNDO, self, activated=self._edit.undo)
        QShortcut(shortcuts.REDO, self, activated=self._edit.redo)
        QShortcut(shortcuts.REDO_ALT_Y, self, activated=self._edit.redo)
        QShortcut(shortcuts.REDO_ALT_SHIFT_Z, self, activated=self._edit.redo)

        # ── Connect signals ──

        # Video widget signals.
        self._player.label_moved.connect(self._on_label_moved)
        self._player.label_resized.connect(self._on_label_resized)
        self._player.label_rotated.connect(self._on_label_rotated)
        self._player.edit_requested.connect(self._on_edit_requested)
        self._player.text_edited.connect(self._on_text_edited)
        self._player.editing_cancelled.connect(self._on_editing_cancelled)
        self._player.context_menu_requested.connect(self._on_context_menu)
        self._player.empty_context_menu_requested.connect(self._on_empty_context_menu)
        self._player.drag_started.connect(self._on_drag_started)
        self._player.drag_finished.connect(self._on_drag_finished)

        # Selection lives in the store. MainWindow listens for changes to
        # reposition the floating toolbar and re-enable bold/italic
        # shortcuts when the selection clears.
        self._store.selection_changed.connect(self._on_store_selection_changed)

        # Gallery signals
        self._gallery.group_selected.connect(self._on_group_selected)
        self._gallery.group_right_clicked.connect(self._on_gallery_context_menu)

        # Keep the legacy ``self._groups`` mirror in sync with the model.
        # MainWindow still indexes into ``self._groups`` from many places
        # (_goto_group, _delete_group, _group_index_for_label, etc.); routing
        # all of those through the model is L1's job, not J2's.
        self._groups_model.groups_changed.connect(self._on_model_groups_changed)

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
        # Mpv-driven timeline + mode sync is owned by PlaybackOrchestrator.
        self._mpv_widget.time_pos_changed.connect(self._playback.on_mpv_time_pos)
        self._mpv_widget.duration_changed.connect(self._on_mpv_duration)
        self._mpv_widget.pause_changed.connect(self._on_mpv_pause_changed)
        self._mpv_widget.eof_reached.connect(self._playback.on_mpv_eof)

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
        """Begin loading a video. Synchronous UI setup happens here; the
        metadata + sidecar fetch is delegated to FileLoader, which fires
        ``_on_file_loaded`` once the bundle is ready.
        """
        self._video_path = path
        self._suppress_resize = suppress_resize
        self._player.set_video(path)
        self._mpv_widget.load(path)
        # Ensure we're in edit mode when loading a new video
        self._playback.set_video_loaded(True)
        self._playback.reset_to_edit()
        _status_msg(self, "Loading...", 0)

        # Hand off the metadata probe + sidecar lookup to FileLoader.
        self._file_loader.load_video(Path(path))

    def _on_file_loaded(self, pair: VideoFilePair) -> None:
        """Apply a FileLoader result: metadata to player/timeline, then
        auto-load the sidecar ASS if present.
        """
        # If the user has navigated to a different file since this load
        # started, the result is stale — ignore it.
        if self._video_path != str(pair.video_path):
            return

        if pair.fps is not None:
            self._player.set_fps(pair.fps)
        if pair.duration is not None and pair.duration > 0:
            self._timeline.set_duration(pair.duration)

        if (not self._suppress_resize
                and pair.width is not None and pair.height is not None):
            self._apply_window_size_for_video(pair.width, pair.height)

        # Auto-load matching .ass (FileLoader already parsed it).
        if pair.ass is not None and pair.ass_path is not None:
            self._apply_loaded_ass(pair.ass, str(pair.ass_path))

        self._update_window_title()
        _status_msg(self, f"Loaded: {self._video_path}")

    def _on_file_load_failed(self, path: object, err: str) -> None:
        QMessageBox.critical(
            self, "Load Failed",
            f"Failed to load video:\n{path}\n\n{err}",
        )
        _status_msg(self, f"Load failed: {path}")

    def _apply_window_size_for_video(self, vid_w: int, vid_h: int) -> None:
        screen_obj = self.screen()
        if not screen_obj:
            return
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

    def _apply_loaded_ass(self, ass: AssFile, path: str) -> None:
        """Install an already-parsed AssFile as the current document.

        Used by both ``_load_ass`` (Open ASS dialog) and ``_on_file_loaded``
        (FileLoader sidecar discovery). All wiring -- player.set_ass, mpv
        subtitle load, store load + gallery refresh, initial group navigation
        -- lives here.
        """
        self._ass = ass
        self._ass_path = path
        self._dirty = False
        self._player.set_ass(self._ass)
        self._mpv_widget.load_subtitles(path)
        self._playback.set_subtitles_path(path)
        self._toolbar.hide()
        _status_msg(self, f"Loaded {len(self._ass.labels)} labels from {path}")

        if self._video_path:
            self._gallery.attach_ass(self._ass)
            self._gallery.attach_video(self._video_path)
            self._store_load_current_ass()
            if self._groups:
                self._goto_group(0)
        else:
            self._groups = []
            self._group_index = -1
            self._player.show_time(self._player._current_time)

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
        self._preload_worker = FolderPreloadWorker(file_paths, self._video_service)
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
        self._video_path = path
        self._player.set_video(path)
        self._player.set_fps(data.fps)
        self._mpv_widget.load(path)
        # Ensure edit mode
        self._playback.set_video_loaded(True)
        self._playback.reset_to_edit()
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
            # _apply_loaded_ass handles all the player/mpv/store wiring.
            # We then seed the pre-rendered thumbnails so the gallery skips
            # extracting them itself, and refire groups_changed so the
            # gallery picks them up.
            self._apply_loaded_ass(data.ass, data.ass.path)
            self._gallery.cache_preloaded_thumbnails(
                data.ass, self._groups_model.groups, data.thumbnails,
            )
            self._groups_model.groups_changed.emit(set())
        else:
            self._ass = None
            self._ass_path = None
            self._groups = []
            self._group_index = -1
            # Reset the gallery (signal-driven). Calling LabelStore.load with
            # an empty AssFile would be the "proper" way, but we don't want to
            # touch the store's source_path. Trigger a transient file_loaded.
            self._gallery._on_file_loaded(None)
            self._groups_model.groups_changed.emit(set())

        self._update_window_title()
        _status_msg(self, f"Loaded: {path}")

    def _open_ass(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open ASS", "", "ASS Subtitles (*.ass);;All (*)"
        )
        if path:
            self._apply_loaded_ass(AssFile(path), path)

    def _save_ass(self) -> None:
        """Save by reconstructing the .ass from the store's state snapshot.

        The on-disk file is rewritten from ``LabelState`` (so the store is
        the source of truth at save time). The previous AssFile's header
        (Script Info + V4+ Styles) is preserved so any hand-edited script
        info / style raw fields survive the round-trip; only the [Events]
        section is regenerated from the state.
        """
        if not self._ass or not self._ass_path:
            _status_msg(self, "No ASS file loaded")
            return
        path = Path(self._ass_path)
        try:
            header = self._ass.lines[:self._ass.events_start_index]
            rebuilt = AssFile.from_state(
                self._store.state,
                header_lines=header,
                play_res_x=self._ass.play_res_x,
                play_res_y=self._ass.play_res_y,
            )
            path.write_bytes(rebuilt.serialize())
        except OSError as e:
            QMessageBox.critical(self, "Save Failed", str(e))
            return
        # Keep the in-memory AssFile in sync with what's on disk so subsequent
        # saves base their header on the freshly written file (and so any
        # code still reading ass.labels / ass.lines sees current values).
        rebuilt.path = str(path)
        self._ass = rebuilt
        self._player.set_ass(self._ass)
        self._gallery.attach_ass(self._ass)
        self._store.mark_clean()
        self._dirty = False
        self._mpv_widget.reload_subtitles()
        self._repopulate_cache()
        _status_msg(self, f"Saved: {path}")

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

    # ── Store/Gallery bridge (J2) ──
    #
    # During the refactor MainWindow still owns ``self._ass`` and mutates it
    # directly. The store needs to know about those mutations so the
    # DerivedGroupModel and Gallery can refresh. These helpers are bridges
    # that will disappear once mutations route through ``store.apply()``
    # (tasks J3/J4/K1).

    def _store_load_current_ass(self) -> None:
        """Push the active ASS file into the store; save serializes from state.

        The gallery still keeps its own AssFile reference (for thumbnail
        rendering / per-style metadata it doesn't get from the store), so
        re-attach it after ``file_loaded`` clears the gallery's ref.
        """
        if self._ass is None or not self._ass_path:
            return
        self._store.load(self._ass, Path(self._ass_path))
        self._gallery.attach_ass(self._ass)

    def _on_model_groups_changed(self, _changed_ids) -> None:
        """Re-sync legacy ``self._groups`` mirror from the gallery's adapter
        list (which itself is built from the model). The timeline subscribes
        to the same signal directly, so no manual refresh needed here."""
        self._groups = self._gallery.groups
        # Clamp current group index.
        if self._group_index >= len(self._groups):
            self._group_index = len(self._groups) - 1
        elif self._group_index < 0 and self._groups:
            self._group_index = -1  # leave unselected; goto called elsewhere

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
        # If in playback mode, snap back to edit mode (without capturing
        # mpv's current frame — the group's representative time will be
        # rendered from ffmpeg instead).
        if self._playback.is_playback:
            self._playback.enter_edit_mode(capture=False)
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
            if self._playback.is_playback:
                self._playback.enter_edit_mode(capture=False)
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

    def _on_store_selection_changed(self, selected_ids: set) -> None:
        """Reposition the toolbar above the new selection (or re-enable
        bold/italic shortcuts when the selection clears).

        The toolbar's display state is managed by LabelToolbar itself via
        its own subscription to ``store.selection_changed``; here we just
        handle the side-effects MainWindow owns (toolbar geometry +
        shortcut enabled state).
        """
        if selected_ids:
            self._update_toolbar_position()
        else:
            self._bold_shortcut.setEnabled(True)
            self._italic_shortcut.setEnabled(True)

    def _on_label_moved(self, label: LabelDialogue, new_x: int, new_y: int) -> None:
        if self._ass and label.label_id:
            self._edit.move(label.label_id, new_x, new_y)
            self._dirty = True
            _status_msg(self, f"Moved \"{label.text}\" to ({new_x}, {new_y})")
        self._update_toolbar_position()

    def _on_label_resized(self, label, new_fs):
        if not label.label_id:
            return
        # The legacy video_widget has already updated ``label.font_size`` in
        # place during the resize drag; route through the controller so the
        # store sees the final size and ass.lines is rewritten.
        self._edit.resize(label.label_id, new_fs)
        self._dirty = True

    def _on_label_rotated(self, label, rotation):
        if not label.label_id:
            return
        self._edit.rotate(label.label_id, rotation)
        self._dirty = True

    def _on_drag_started(self) -> None:
        self._toolbar.hide()

    def _on_drag_finished(self) -> None:
        # Toolbar self-syncs from labels_mutated; re-show it (selection is
        # unchanged so it stayed in sync). Then position it above the label.
        if self._player.selected_labels():
            self._toolbar.show()
            self._update_toolbar_position()

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
        if not label.label_id:
            return
        self._edit.duplicate(label.label_id)
        self._player.show_time(self._player._current_time)
        self._dirty = True
        _status_msg(self, f"Duplicated \"{label.text}\"")

    def _on_sync_times(self, labels: list) -> None:
        if not self._ass or len(labels) < 2:
            return
        start = min(lb.start_time for lb in labels)
        end = max(lb.end_time for lb in labels)
        ids = [lb.label_id for lb in labels if lb.label_id]
        self._edit.retime_many(ids, start, end)
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
        removed_ids = {lb.label_id for lb in selected if lb.label_id}
        # Controller submits DeleteLabel(s) and mirrors onto AssFile.
        # The labels_removed signal triggers gallery + groups rebuild +
        # self._on_model_groups_changed which refreshes self._groups.
        self._edit.delete(removed_ids)
        # Navigate to a sensible group after the rebuild.
        if gi >= 0:
            if not self._groups:
                self._group_index = -1
                self._player.show_time(self._player._current_time)
            else:
                next_gi = min(gi, len(self._groups) - 1)
                self._goto_group(next_gi)
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
        removed_ids = {lb.label_id for lb in group.labels if lb.label_id}
        self._edit.delete(removed_ids)
        self._advance_after_group_removal(gi)
        self._dirty = True
        _status_msg(self, f"Deleted {count} label{'s' if count > 1 else ''}")

    def _advance_after_group_removal(self, gi: int) -> None:
        """Pick a sensible group to navigate to after one was removed."""
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
        selected = list(self._player.selected_labels())
        ids = {lb.label_id for lb in selected if lb.label_id}
        self._edit.change_style_many(ids, StylePatch(font_size=size))
        self._dirty = True
        self._player.update()

    def _on_alignment_changed(self, new_alignment: int) -> None:
        if not self._ass:
            return
        selected = self._player.selected_labels()
        if not selected:
            return
        # Compute new positions per label before submitting (geometry depends
        # on current font/rect). Submit alignment change then position move
        # for each label.
        for label in selected:
            if not label.label_id:
                continue
            font = self._player._font_for_label(label)
            rect = self._player._compute_rect(label, font)
            new_anchor = _anchor_for_alignment(rect, new_alignment)
            new_x, new_y = self._player._widget_to_ass(new_anchor.x(), new_anchor.y())
            self._edit.change_style(label.label_id, StylePatch(alignment=new_alignment))
            self._edit.move(label.label_id, new_x, new_y)
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
        # Build a StylePatch for the patch-compatible fields. style_name and
        # position are handled separately via dedicated controller methods.
        patch = StylePatch(
            font_size=clip["font_size"],
            primary_colour=clip["primary_colour"],
            outline_colour=clip["outline_colour"],
            outline_width=clip["outline_width"],
            bold=clip["bold"],
            italic=clip["italic"],
            rotation=clip["rotation"],
            # alignment is handled below (it requires a recomputed position).
        )
        style_name = clip["style"]
        position = clip["position"]
        alignment = clip["alignment"]
        selected_for_paste = list(self._player.selected_labels())
        ids = {lb.label_id for lb in selected_for_paste if lb.label_id}
        # Apply the bulk StylePatch fields as one batch.
        if ids and any(
            getattr(patch, f) is not None
            for f in ("font_size", "primary_colour", "outline_colour",
                      "outline_width", "bold", "italic", "rotation")
        ):
            self._edit.change_style_many(ids, patch)
        # Named-style change is not a StylePatch field; route via the
        # controller's dedicated helper.
        if style_name and style_name in self._ass.styles:
            for lid in ids:
                self._edit.change_style_name(lid, style_name)
        # Position paste.
        if position is not None:
            for lid in ids:
                self._edit.move(lid, position[0], position[1])
        # Alignment paste also moves the label to keep its visual anchor.
        if alignment is not None:
            for label in selected_for_paste:
                if not label.label_id:
                    continue
                font = self._player._font_for_label(label)
                rect = self._player._compute_rect(label, font)
                new_anchor = _anchor_for_alignment(rect, alignment)
                new_x, new_y = self._player._widget_to_ass(new_anchor.x(), new_anchor.y())
                self._edit.change_style(label.label_id, StylePatch(alignment=alignment))
                self._edit.move(label.label_id, new_x, new_y)
        self._dirty = True
        self._player._font_corrections.clear()
        self._player.update()
        self._refresh_toolbar_for_selection()
        pasted = [k for k, v in clip.items() if v is not None]
        _status_msg(self, f"Pasted: {', '.join(pasted) if pasted else 'nothing'}")

    def _on_bold_toggled(self, bold: bool) -> None:
        if not self._ass:
            return
        ids = {lb.label_id for lb in self._player.selected_labels() if lb.label_id}
        self._edit.change_style_many(ids, StylePatch(bold=bold))
        self._dirty = True
        self._player.update()
        self._update_toolbar_position()

    def _on_italic_toggled(self, italic: bool) -> None:
        if not self._ass:
            return
        ids = {lb.label_id for lb in self._player.selected_labels() if lb.label_id}
        self._edit.change_style_many(ids, StylePatch(italic=italic))
        self._dirty = True
        self._player.update()
        self._update_toolbar_position()

    def _on_style_changed(self, style_name: str) -> None:
        if not self._ass or style_name not in self._ass.styles:
            return
        for label in list(self._player.selected_labels()):
            if label.label_id:
                self._edit.change_style_name(label.label_id, style_name)
        self._dirty = True
        self._player._font_corrections.clear()
        self._player.update()
        self._refresh_toolbar_for_selection()

    def _on_primary_colour_changed(self, colour: str) -> None:
        if not self._ass:
            return
        ids = {lb.label_id for lb in self._player.selected_labels() if lb.label_id}
        self._edit.change_style_many(ids, StylePatch(primary_colour=colour))
        self._dirty = True
        self._player.update()

    def _on_outline_colour_changed(self, colour: str) -> None:
        if not self._ass:
            return
        ids = {lb.label_id for lb in self._player.selected_labels() if lb.label_id}
        self._edit.change_style_many(ids, StylePatch(outline_colour=colour))
        self._dirty = True
        self._player.update()

    def _on_outline_width_changed(self, width: float) -> None:
        if not self._ass:
            return
        ids = {lb.label_id for lb in self._player.selected_labels() if lb.label_id}
        self._edit.change_style_many(ids, StylePatch(outline_width=width))
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
        # Mirror into the store's state.styles so toolbar lookups + save
        # serialization see the new style (state.styles is a shallow copy of
        # ass.styles at load time, so adds don't propagate automatically).
        self._store.state.styles[name] = new_style
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
            # set_label_style + remove_inline_tag mutated the LabelDialogue
            # in place; emit labels_mutated so the toolbar / video widget /
            # gallery refresh.
            if label.label_id:
                self._store.labels_mutated.emit({label.label_id})
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

        # remove_inline_tag_from_all mutated all labels using the style in
        # place. Notify subscribers so the toolbar / video widget refresh.
        affected = {
            lid for lid, dlg in self._store.state.labels.items()
            if dlg.style_name == style_name
        }
        if affected:
            self._store.labels_mutated.emit(affected)
        self._dirty = True
        self._player.update()
        self._refresh_toolbar_for_selection()
        _status_msg(self, f"Applied overrides to style '{style_name}'")

    def _refresh_toolbar_for_selection(self) -> None:
        """Reposition the toolbar above the current selection.

        Display state is auto-updated by the toolbar from ``labels_mutated``.
        Callers invoke this after edits that may have changed the label rect
        (font size / style swap / paste) so the toolbar follows the new rect.
        """
        if self._player.selected_labels():
            self._update_toolbar_position()

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
        ids = {lb.label_id for lb in selected if lb.label_id}
        self._edit.change_style_many(ids, StylePatch(bold=new_bold))
        self._dirty = True
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
        ids = {lb.label_id for lb in selected if lb.label_id}
        self._edit.change_style_many(ids, StylePatch(italic=new_italic))
        self._dirty = True
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
        if not self._ass or not label.label_id:
            return
        # The display text is rich_text minus inline override blocks; compute
        # it here so the controller submits both text and rich_text atomically.
        from sub_label_pos.model.ass_file import _OVERRIDE_BLOCK_RE
        display_text = _OVERRIDE_BLOCK_RE.sub("", rich_text).strip()
        self._edit.edit_text(label.label_id, display_text, rich_text)
        self._dirty = True
        self._player.show_time(self._player._current_time)
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
        ids = [lb.label_id for lb in labels if lb.label_id]
        merged_id = self._edit.merge(ids, order, separator)
        if merged_id is None:
            return
        merged_dlg = self._store.state.labels.get(merged_id)
        if gi >= 0 and 0 <= gi < len(self._groups):
            self._goto_group(gi)
        self._dirty = True
        preview = (merged_dlg.text[:30] if merged_dlg else "")
        _status_msg(self, f"Merged {len(labels)} labels into \"{preview}\"")

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
        new_id = self._edit.create_label(
            pos_x=ass_x, pos_y=ass_y,
            start_time=start_time, end_time=end_time,
            text="New Label",
            font_size=self._ass.label_font_size,
        )
        if new_id is None:
            return
        # Locate the freshly added label via the store for group navigation.
        new_label = self._store.state.labels.get(new_id)
        if new_label is not None:
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

    def _on_playback_mode_changed(self, mode: str) -> None:
        """React to orchestrator mode changes for UI side-effects.

        The orchestrator handles the heavy lifting (stack switch, mpv
        play/pause, timeline state); MainWindow only handles things tied
        to widgets it owns directly, e.g. hiding the floating toolbar.
        """
        if mode == "playback":
            self._toolbar.hide()

    def _toggle_playback(self) -> None:
        """Toggle between playback and edit modes (Space bar)."""
        # Don't toggle if inline text editor is active
        if self._player._text_edit is not None:
            return
        if not self._video_path:
            return
        self._playback.toggle()

    def _on_play_toggled(self, playing: bool) -> None:
        """Handle play/pause button from timeline widget."""
        if playing:
            self._playback.enter_playback_mode()
        else:
            self._playback.enter_edit_mode()

    def _on_timeline_seeked(self, seconds: float) -> None:
        """Handle scrubber drag from timeline."""
        self._playback_from_group = False
        if self._playback.is_playback:
            # Drop back to edit mode (without capturing mpv's frame — we
            # are about to seek), then point mpv at the new position so a
            # subsequent play resumes from there.
            self._playback.enter_edit_mode(capture=False)
            self._mpv_widget.seek_absolute(seconds)
        self._player.show_time(seconds)

    def _on_timeline_step(self, delta: int) -> None:
        """Handle frame step buttons from timeline."""
        self._playback_from_group = False
        if self._playback.is_playback:
            self._playback.enter_edit_mode()
        if delta > 0:
            self._player.step_frame(1)
        else:
            self._player.step_frame(-1)
        self._timeline.set_time(self._player._current_time)

    def _on_step_forward(self) -> None:
        """Arrow right — frame step in current mode."""
        self._playback_from_group = False
        if self._playback.is_playback:
            self._mpv_widget.frame_step(forward=True)
        else:
            self._player.step_frame(1)
            self._timeline.set_time(self._player._current_time)

    def _on_step_backward(self) -> None:
        """Arrow left — frame step in current mode."""
        self._playback_from_group = False
        if self._playback.is_playback:
            self._mpv_widget.frame_step(forward=False)
        else:
            self._player.step_frame(-1)
            self._timeline.set_time(self._player._current_time)

    def _on_mpv_duration(self, duration: float) -> None:
        """Update timeline when mpv reports video duration."""
        self._timeline.set_duration(duration)

    def _on_mpv_pause_changed(self, paused: bool) -> None:
        """Handle mpv pause state changes."""
        self._timeline.set_playing(not paused)

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
        self._file_loader.shutdown()
        self._gallery._cancel_loading()
        self._gallery._cancel_single_refresh()
        self._mpv_widget.shutdown()
        self._player.shutdown()
        self._frame_queue.shutdown()
        super().closeEvent(event)
