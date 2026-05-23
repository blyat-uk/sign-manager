from __future__ import annotations

import itertools
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

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
    QProgressBar,
    QStackedWidget,
    QStyle,
    QDialog,
    QDialogButtonBox,
    QCheckBox,
    QComboBox,
    QToolButton,
    QSizePolicy,
)

from sub_label_pos.model.ass_file import (
    AssFile, LabelDialogue, _seconds_to_time,
    _FS_TAG_RE, _AN_TAG_RE, _B_TAG_RE, _I_TAG_RE,
    _C_TAG_RE, _3C_TAG_RE, _BORD_TAG_RE,
)
from sub_label_pos.model.groups import DerivedGroupModel
from sub_label_pos.model.label_store import LabelStore
from sub_label_pos.services.app_settings import (
    load as load_settings,
    redetect as redetect_settings,
    save_perf as save_perf_settings,
)
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
from sub_label_pos.ui.controllers.retime_controller import RetimeController
from sub_label_pos.ui.retime_bar import RetimeBar
from sub_label_pos.ui.focused_timeline import FocusedTimeline
from sub_label_pos.ui.video_widget import VideoFrameWidget
from sub_label_pos.ui.gallery_widget import GalleryPanel, LabelGroup, compute_label_groups, _crop_to_labels_image, _best_representative_time
from sub_label_pos.ui.label_toolbar import LabelToolbar
from sub_label_pos.ui.timeline_widget import TimelineWidget
from sub_label_pos.ui import theme
from sub_label_pos.ui.labels_sidebar import LabelsSidebar
from sub_label_pos.ui.settings_dialog import SettingsDialog

_VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".webm"}


def _anchor_for_alignment(rect: QRectF, alignment: int) -> QPointF:
    """Return the anchor point of *rect* for the given ASS numpad alignment."""
    h_align = ((alignment - 1) % 3) + 1
    v_group = (alignment - 1) // 3
    ax = rect.x() if h_align == 1 else (rect.right() if h_align == 3 else rect.center().x())
    ay = rect.bottom() if v_group == 0 else (rect.center().y() if v_group == 1 else rect.top())
    return QPointF(ax, ay)


def _format_hms(seconds: float) -> str:
    total_ms = int(seconds * 1000)
    h = total_ms // 3_600_000
    m = (total_ms // 60_000) % 60
    s = (total_ms // 1000) % 60
    ms = total_ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


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

    def __init__(
        self,
        path: str,
        video_service: VideoService,
        *,
        generate_thumbnails: bool = True,
        subs_dir: Path | None = None,
    ) -> None:
        super().__init__()
        self.signals = _RichPreloadSignals()
        self._path = path
        self._svc = video_service
        self._cancelled = False
        # When False, skip per-group thumbnail extraction entirely. Saves
        # significant RAM on Performance-tier machines when the gallery is
        # hidden — preloaded QImage thumbnails would otherwise be retained
        # in MainWindow._preloaded for the lifetime of the folder.
        self._gen_thumbs = generate_thumbnails
        self._subs_dir = subs_dir

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
            ass_path = FileLoader.find_ass_sidecar(video_path, subs_dir=self._subs_dir)
            if ass_path is not None:
                ass_file = AssFile(str(ass_path))

            # --- Label groups + per-group thumbnails ---
            groups: list[LabelGroup] = []
            thumbnails: dict[int, QImage] = {}

            if ass_file and not self._cancelled:
                groups = compute_label_groups(ass_file.labels)
                if self._gen_thumbs:
                    for i, group in enumerate(groups):
                        if self._cancelled:
                            break
                        try:
                            img = self._svc.get_frame(
                                video_path, group.representative_time
                            )
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
    """Manages parallel file preloading using QThreadPool.

    When ``ring > 0``, only files within ``[active - ring, active + ring]``
    of the currently-active file (set via :meth:`set_active_index` /
    :meth:`set_active_path`) are queued for preload. Calling
    ``set_active_index`` after construction shifts the ring and queues
    any new ring members that haven't been submitted yet; in-flight
    tasks outside the new ring are not cancelled — their results still
    populate the cache.

    When ``ring == 0`` (default), every file is queued at start — the
    legacy behaviour.
    """
    file_ready = pyqtSignal(str, object)  # path, PreloadedFileData
    all_done = pyqtSignal()

    _DEFAULT_POOL_SIZE = 5

    def __init__(
        self,
        file_paths: list[str],
        video_service: VideoService,
        workers: int | None = None,
        ring: int = 0,
        generate_thumbnails: bool = True,
        subs_dir: Path | None = None,
    ):
        super().__init__()
        self._file_paths = file_paths
        self._video_service = video_service
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(workers or self._DEFAULT_POOL_SIZE)
        self._total = len(file_paths)
        self._completed = 0
        self._tasks: list[_RichFilePreloadTask] = []
        self._ring = max(0, int(ring))
        self._active_index = 0
        self._queued: set[int] = set()
        self._started = False
        self._gen_thumbs = generate_thumbnails
        self._subs_dir = subs_dir

    def _compute_ring_indices(self, active: int) -> set[int]:
        """Return the set of file indices that should be preloaded for ``active``.

        For ring == 0, this is every index (legacy behaviour). Otherwise
        the window is clipped to ``[0, N-1]`` — so a click on the first
        or last file produces an asymmetric (clipped) window rather than
        wrapping or padding.
        """
        n = self._total
        if n == 0:
            return set()
        if self._ring <= 0:
            return set(range(n))
        active = max(0, min(int(active), n - 1))
        lo = max(0, active - self._ring)
        hi = min(n - 1, active + self._ring)
        return set(range(lo, hi + 1))

    def _queue_indices(self, indices: set[int]) -> None:
        """Submit any indices not already queued, in active-distance order."""
        new = sorted(
            (i for i in indices if i not in self._queued),
            key=lambda i: (abs(i - self._active_index), i),
        )
        for i in new:
            path = self._file_paths[i]
            task = _RichFilePreloadTask(
                path, self._video_service,
                generate_thumbnails=self._gen_thumbs,
                subs_dir=self._subs_dir,
            )
            task.signals.file_ready.connect(self.file_ready)
            task.signals.finished.connect(self._on_task_finished)
            self._tasks.append(task)
            self._queued.add(i)
            self._pool.start(task)

    def set_active_index(self, index: int) -> None:
        """Re-centre the preload ring on ``index`` and queue any new members."""
        if self._total == 0:
            return
        self._active_index = max(0, min(int(index), self._total - 1))
        if not self._started:
            return
        self._queue_indices(self._compute_ring_indices(self._active_index))

    def set_active_path(self, path: str) -> None:
        """Convenience: look up ``path`` in the worker's file list and re-centre."""
        try:
            idx = self._file_paths.index(path)
        except ValueError:
            return
        self.set_active_index(idx)

    def start(self):
        if not self._file_paths:
            self.all_done.emit()
            return
        self._started = True
        self._queue_indices(self._compute_ring_indices(self._active_index))

    def _on_task_finished(self):
        self._completed += 1
        # all_done fires when every QUEUED task has finished, not every
        # file in the folder — with a ring, most files are never queued.
        if self._completed >= len(self._queued):
            self.all_done.emit()

    def cancel(self):
        for task in self._tasks:
            task.cancel()
        self._pool.clear()
        self._pool.waitForDone(3000)


class _FileRowWidget(QWidget):
    """Sidebar file row: [status dot] filename."""

    READY = "ready"
    PENDING = "pending"

    def __init__(self, filename: str, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 3, 8, 3)
        layout.setSpacing(7)

        self._dot = QLabel()
        self._dot.setFixedSize(8, 8)
        self._name = QLabel(filename)
        self._name.setStyleSheet(
            f"color: {theme.Tokens.text_primary}; font-size: 11px; background: transparent;"
        )
        layout.addWidget(self._dot)
        layout.addWidget(self._name, 1)

        self._state = self.PENDING
        self._apply_dot()

    def set_state(self, state: str) -> None:
        if self._state == state:
            return
        self._state = state
        self._apply_dot()

    def _apply_dot(self) -> None:
        if self._state == self.READY:
            self._dot.setStyleSheet(
                f"background: {theme.Tokens.accent}; border-radius: 4px;"
            )
        else:
            self._dot.setStyleSheet(
                f"background: transparent; "
                f"border: 1px solid {theme.Tokens.border_strong}; "
                f"border-radius: 4px;"
            )


class WelcomeWidget(QWidget):
    """Start screen shown when no file is loaded."""

    open_video_clicked = pyqtSignal()
    open_folder_clicked = pyqtSignal()
    recent_directory_clicked = pyqtSignal(str)
    clear_all_clicked = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        from sub_label_pos.ui import theme

        self.setStyleSheet(f"background: {theme.Tokens.bg_base};")

        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        outer.setContentsMargins(40, 48, 40, 36)
        outer.setSpacing(theme.Tokens.sp_5)

        # --- Hero ---
        hero = QWidget()
        hero.setStyleSheet("background: transparent;")
        hero_layout = QVBoxLayout(hero)
        hero_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hero_layout.setSpacing(6)

        title_row = QWidget()
        title_row.setStyleSheet("background: transparent;")
        title_row_layout = QHBoxLayout(title_row)
        title_row_layout.setSpacing(12)
        title_row_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_icon = QLabel()
        title_icon.setStyleSheet("background: transparent;")
        title_icon.setPixmap(
            theme.Icons.app_logo(color=theme.Tokens.accent).pixmap(QSize(28, 28))
        )
        title_text = QLabel("Sub Label Pos")
        title_text.setStyleSheet(
            f"color: {theme.Tokens.text_emphasis}; font-size: {theme.Tokens.text_hero}px; "
            f"font-weight: 700; letter-spacing: -0.3px; background: transparent;"
        )
        title_row_layout.addWidget(title_icon)
        title_row_layout.addWidget(title_text)
        hero_layout.addWidget(title_row)

        subtitle = QLabel("Visually position labels in .ass subtitle files — drag, snap, save.")
        subtitle.setStyleSheet(
            f"color: {theme.Tokens.text_muted}; font-size: 13px; background: transparent;"
        )
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hero_layout.addWidget(subtitle)
        outer.addWidget(hero)

        # --- Recent panel ---
        panel = QWidget()
        panel.setFixedWidth(540)
        panel.setStyleSheet("background: transparent;")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(8)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(4, 0, 4, 0)
        eyebrow = QLabel("RECENT FOLDERS")
        eyebrow.setStyleSheet(
            f"color: {theme.Tokens.text_muted}; font-size: 10.5px; "
            f"text-transform: uppercase; letter-spacing: 0.6px; "
            f"font-weight: 700; background: transparent;"
        )
        clear_btn = QLabel("✕ Clear all")
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.setStyleSheet(
            f"color: {theme.Tokens.border_strong}; font-size: 11px; background: transparent;"
        )
        clear_btn.mousePressEvent = lambda _e: self._on_clear_all_clicked()
        header_row.addWidget(eyebrow)
        header_row.addStretch()
        header_row.addWidget(clear_btn)
        panel_layout.addLayout(header_row)

        self._list = QListWidget()
        self._list.setStyleSheet(f"""
            QListWidget {{
                background: {theme.Tokens.bg_surface};
                border: 1px solid {theme.Tokens.border};
                border-radius: 7px;
                outline: none;
            }}
            QListWidget::item {{
                border-bottom: 1px solid #232629;
            }}
            QListWidget::item:last-child {{ border-bottom: none; }}
            QListWidget::item:selected {{
                background: {theme.Tokens.accent_deep};
            }}
            QListWidget::item:hover:!selected {{
                background: {theme.Tokens.bg_raised};
            }}
        """)
        self._list.itemDoubleClicked.connect(self._on_item_activated)
        self._list.itemActivated.connect(self._on_item_activated)
        self._list.installEventFilter(self)
        panel_layout.addWidget(self._list)

        self._empty_label = QLabel("\U0001f4c1 No recent folders yet — open a video or folder below to get started.")
        self._empty_label.setStyleSheet(
            f"color: {theme.Tokens.border_strong}; font-size: 12px; padding: 30px 20px; "
            f"background: {theme.Tokens.bg_surface}; border: 1px solid {theme.Tokens.border}; "
            f"border-radius: 7px;"
        )
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.hide()
        panel_layout.addWidget(self._empty_label)

        outer.addWidget(panel, alignment=Qt.AlignmentFlag.AlignHCenter)

        # --- Action buttons ---
        actions_row = QHBoxLayout()
        actions_row.setSpacing(10)
        actions_row.setAlignment(Qt.AlignmentFlag.AlignCenter)

        btn_video = theme.IconButton(
            theme.Icons.file_video(), "Open video",
            tooltip="Open a video file (.mkv, .mp4, .avi, .webm)",
        )
        btn_video.setFixedHeight(38)
        btn_video.setStyleSheet(
            btn_video.styleSheet()
            + f" QPushButton {{ background: {theme.Tokens.bg_raised}; "
              f"border: 1px solid {theme.Tokens.border}; padding: 0 22px; }}"
        )
        btn_video.clicked.connect(self.open_video_clicked)

        btn_folder = theme.IconButton(
            theme.Icons.open_folder(), "Open folder",
            tooltip="Open a folder of videos (folder mode)",
        )
        btn_folder.setFixedHeight(38)
        btn_folder.setStyleSheet(btn_video.styleSheet())
        btn_folder.clicked.connect(self.open_folder_clicked)

        actions_row.addWidget(btn_video)
        actions_row.addWidget(btn_folder)
        outer.addLayout(actions_row)

        # --- Drop hint ---
        drop_hint = QLabel("↓ or drag a video onto the window")
        drop_hint.setStyleSheet(
            f"color: {theme.Tokens.border_strong}; font-size: 11px; background: transparent;"
        )
        drop_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(drop_hint)

        self._dirs: list[dict] = []

    def set_recent_dirs(self, dirs: list) -> None:
        """Accepts list[str] (legacy) or list[dict]. Migrates internally."""
        from sub_label_pos.ui import theme
        from sub_label_pos.ui.recent_dirs import migrate
        from datetime import datetime

        self._dirs = migrate(dirs)
        self._list.clear()
        if not self._dirs:
            self._list.hide()
            self._empty_label.show()
            return
        self._list.show()
        self._empty_label.hide()

        for entry in self._dirs:
            p = Path(entry["path"])
            last_opened = None
            if entry.get("last_opened"):
                try:
                    last_opened = datetime.fromisoformat(entry["last_opened"])
                except ValueError:
                    last_opened = None
            widget = theme.RecentItemWidget(
                name=p.name,
                path=str(p),
                file_count=entry.get("file_count"),
                last_opened=last_opened,
            )
            item = QListWidgetItem()
            item.setSizeHint(QSize(520, 56))
            item.setData(Qt.ItemDataRole.UserRole, entry["path"])
            self._list.addItem(item)
            self._list.setItemWidget(item, widget)

    def _on_item_activated(self, item: QListWidgetItem | None) -> None:
        # itemActivated can fire without a current item in some keyboard
        # scenarios (e.g. Enter on an empty selection); guard before deref.
        if item is None:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            self.recent_directory_clicked.emit(path)

    def _on_clear_all_clicked(self) -> None:
        if QMessageBox.question(
            self, "Clear recent folders", "Remove all recent folders?"
        ) == QMessageBox.StandardButton.Yes:
            self.clear_all_clicked.emit()

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
        self.menuBar().hide()
        self.resize(1280, 720)
        self.setAcceptDrops(True)

        # Load persisted perf settings (runs first-run hardware detection if
        # no settings file exists). Drives FrameCache size, FrameRequestQueue
        # worker count, and gallery thumbnail dimensions/quality below.
        self._app_settings = load_settings()
        perf = self._app_settings.perf
        log.info(
            "perf tier=%s (RAM=%.1f GB, cores=%d): "
            "thumb_max=%d q=%d cache=%d preload=%d queue=%d",
            self._app_settings.hardware_tier,
            self._app_settings.detected_ram_gb,
            self._app_settings.detected_cpu_cores,
            perf.thumb_max_dim, perf.thumb_jpeg_quality, perf.frame_cache_size,
            perf.preload_workers, perf.frame_queue_workers,
        )

        # Video service stack (frame extraction + LRU cache + async queue)
        self._video_service: VideoService = CachedVideoService(
            FFmpegVideoService(), FrameCache(max_size=perf.frame_cache_size),
        )
        self._frame_queue = FrameRequestQueue(
            self._video_service, workers=perf.frame_queue_workers
        )

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
        self._retime = RetimeController(
            self._store, self._edit,
            fps_provider=lambda: getattr(self._player, "_fps", 0.0),
            current_time_provider=lambda: getattr(self._player, "_current_time", 0.0),
            seek_callback=self._seek_player_to,
        )

        self._ass: AssFile | None = None
        self._groups: list[LabelGroup] = []
        self._group_index: int = -1
        self._playback_from_group: bool = True
        self._video_path: str | None = None
        self._ass_path: str | None = None
        # When the user opens a folder whose videos have no local .ass
        # sidecars, they're prompted to pick a separate subs folder. That
        # folder is held here for the lifetime of the open folder and reset
        # every time a new folder is opened. Never persisted.
        self._subs_dir: Path | None = None
        self._style_clipboard: dict | None = None
        self.__dirty: bool = False
        # Per-label dirty tracker. Accumulates on every labels_mutated;
        # cleared on file_loaded and on successful _save_ass. Shared with
        # the labels sidebar (and, opportunistically, the gallery thumbs).
        self._dirty_label_ids: set = set()
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
        self._file_row_widgets: dict[str, _FileRowWidget] = {}

        # ── Files sidebar (hidden by default) ──
        self._files_dock = QDockWidget("Files", self)
        self._files_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        # No close/float buttons; cannot be dragged out to a floating window.
        self._files_dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        dock_content = QWidget()
        dock_content.setStyleSheet(f"background: {theme.Tokens.bg_deepest};")
        dock_layout = QVBoxLayout(dock_content)
        dock_layout.setContentsMargins(6, 6, 6, 6)
        dock_layout.setSpacing(4)

        self._folder_path_label = QLabel()
        self._folder_path_label.setStyleSheet(
            f"color: {theme.Tokens.text_muted}; font-size: 11px; background: transparent;"
        )
        self._folder_path_label.setWordWrap(False)
        dock_layout.addWidget(self._folder_path_label)

        self._folder_progress_label = QLabel()
        self._folder_progress_label.setStyleSheet(
            f"color: {theme.Tokens.text_primary}; font-size: 11px; background: transparent;"
        )
        dock_layout.addWidget(self._folder_progress_label)

        # Hair-line preload progress bar
        self._preload_bar = QProgressBar()
        self._preload_bar.setFixedHeight(2)
        self._preload_bar.setTextVisible(False)
        self._preload_bar.setRange(0, 100)
        self._preload_bar.setValue(0)
        self._preload_bar.setStyleSheet(
            f"QProgressBar {{ background: {theme.Tokens.border}; border: none; }}"
            f"QProgressBar::chunk {{ background: {theme.Tokens.accent}; }}"
        )
        self._preload_bar.hide()
        dock_layout.addWidget(self._preload_bar)

        self._file_list = QListWidget()
        self._file_list.setStyleSheet(f"""
            QListWidget {{
                background: {theme.Tokens.bg_surface};
                color: {theme.Tokens.text_primary};
                border: 1px solid {theme.Tokens.border};
                border-radius: 5px;
                font-size: 11px;
                outline: none;
            }}
            QListWidget::item {{ padding: 0; }}
            QListWidget::item:selected {{
                background: {theme.Tokens.accent_deep};
                color: {theme.Tokens.text_emphasis};
            }}
        """)
        dock_layout.addWidget(self._file_list)

        self._files_dock.setWidget(dock_content)
        self._files_dock.setMinimumWidth(200)
        self._files_dock.setMaximumWidth(360)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self._files_dock)
        self._files_dock.hide()
        self._file_list.currentRowChanged.connect(self._on_file_list_clicked)

        # ── Labels sidebar (right) ──
        self._labels_sidebar = LabelsSidebar(self._store, parent=self)
        self._labels_dock = QDockWidget("Labels", self)
        self._labels_dock.setAllowedAreas(Qt.DockWidgetArea.RightDockWidgetArea)
        self._labels_dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        self._labels_dock.setTitleBarWidget(QWidget())  # hide native title bar
        self._labels_dock.setWidget(self._labels_sidebar)
        self._labels_dock.setMinimumWidth(240)
        self._labels_dock.setMaximumWidth(400)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._labels_dock)
        # Hidden until the editor page is active (mirrors _main_tb). The
        # show happens in _switch_to_editor, gated on the user's preference.
        self._labels_dock.hide()

        self._labels_sidebar.row_clicked.connect(self._on_labels_row_clicked)
        self._labels_sidebar.row_jump_requested.connect(self._on_labels_row_jump)
        self._labels_sidebar.row_edit_requested.connect(self._on_edit_requested)
        self._labels_sidebar.row_delete_requested.connect(self._delete_labels)

        # Layout: splitter with video stack + timeline on top, gallery on bottom
        self._mpv_widget = MpvPreviewWidget()
        # Apply saved mpv settings before GL init (initializeGL is lazy).
        # ``mpv/hwdec`` lives in QSettings (hardware-policy, per-machine).
        # ``mpv_quality`` lives in PerfSettings (tier-aware, persisted there).
        _init_settings = QSettings("SubLabelPos", "SubLabelPos")
        self._init_settings = _init_settings
        self._mpv_widget._hwdec = _init_settings.value("mpv/hwdec", "auto-safe")
        self._mpv_widget._hq = (self._app_settings.perf.mpv_quality == "high")
        self._player = VideoFrameWidget(
            self._store,
            self._video_service,
            frame_queue=self._frame_queue,
            frame_cache_size=perf.frame_cache_size,
        )
        self._gallery = GalleryPanel(
            store=self._store,
            groups=self._groups_model,
            video_service=self._video_service,
            thumb_max_dim=perf.thumb_max_dim,
            thumb_jpeg_quality=perf.thumb_jpeg_quality,
        )
        self._timeline = TimelineWidget(groups=self._groups_model)
        # Floating overlay tray: children of self._player so they overlay
        # the canvas rather than taking layout space (selection toggles no
        # longer make the canvas/timeline jump up and down).
        self._retime_bar = RetimeBar(self._store, self._retime, parent=self._player)
        self._focused_timeline = FocusedTimeline(
            self._store, self._retime,
            fps_provider=lambda: getattr(self._player, "_fps", 0.0),
            parent=self._player,
        )

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

        # Container for video stack + retime bar + focused timeline + timeline
        # Order: 1. video stack (canvas), 2. RetimeBar, 3. FocusedTimeline,
        #        4. main TimelineWidget
        video_container = QWidget()
        vc_layout = QVBoxLayout(video_container)
        vc_layout.setContentsMargins(0, 0, 0, 0)
        vc_layout.setSpacing(0)
        vc_layout.addWidget(self._video_stack, 1)
        # RetimeBar + FocusedTimeline are NOT in this layout — they're floating
        # children of self._player so they don't shift the canvas on toggle.
        # _layout_floating_retime_tray() positions them at the bottom of the canvas.
        vc_layout.addWidget(self._timeline, 0)

        self._splitter = QSplitter(Qt.Orientation.Vertical)
        self._splitter.addWidget(video_container)
        from PyQt6.QtWidgets import QWidget as _Widget, QVBoxLayout as _VBox
        from sub_label_pos.ui.gallery_widget import GalleryHandle
        self._gallery_container = _Widget()
        gallery_layout = _VBox(self._gallery_container)
        gallery_layout.setContentsMargins(0, 0, 0, 0)
        gallery_layout.setSpacing(0)
        self._gallery_handle = GalleryHandle()
        self._gallery_handle.toggled.connect(self._on_gallery_handle_toggled)
        gallery_layout.addWidget(self._gallery_handle)
        gallery_layout.addWidget(self._gallery)
        self._splitter.addWidget(self._gallery_container)
        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 0)
        splitter = self._splitter  # alias for the remaining setup below

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

        # Apply initial gallery visibility from display settings.
        self._gallery.setVisible(self._app_settings.display.gallery_visible)

        # --- Main toolbar ---
        self._main_tb = QToolBar("Main")
        self._main_tb.setMovable(False)
        self._main_tb.setIconSize(QSize(18, 18))
        self._main_tb.setStyleSheet(
            f"QToolBar {{ background: {theme.Tokens.bg_raised}; "
            f"border-bottom: 1px solid {theme.Tokens.border}; "
            f"padding: 4px 8px; spacing: 4px; }}"
        )
        self.addToolBar(self._main_tb)

        # Open (split-button menu)
        open_btn = QToolButton()
        open_btn.setIcon(theme.Icons.open_folder())
        open_btn.setText("Open")
        open_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        open_btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        open_btn.setToolTip("Open video, folder, or ASS")
        open_menu = QMenu(open_btn)
        open_menu.addAction("Open video…", self._open_video)
        open_menu.addAction("Open folder…", self._open_folder)
        open_menu.addAction("Open ASS only…", self._open_ass)
        open_btn.setMenu(open_menu)
        open_btn.clicked.connect(self._open_video)  # default action
        self._main_tb.addWidget(open_btn)

        # Save (primary)
        self._save_btn = theme.PrimaryButton(
            "Save", icon=theme.Icons.save(),
            tooltip=f"Save ({shortcuts.SAVE.toString()})",
        )
        self._save_btn.clicked.connect(self._save_ass)
        self._main_tb.addWidget(self._save_btn)

        self._main_tb.addSeparator()

        # Undo / Redo
        self._undo_btn = theme.IconButton(
            theme.Icons.undo(),
            tooltip=f"Undo ({shortcuts.UNDO.toString()})",
            icon_only=True,
        )
        self._undo_btn.clicked.connect(self._edit.undo)
        self._undo_btn.setEnabled(False)
        self._redo_btn = theme.IconButton(
            theme.Icons.redo(),
            tooltip=f"Redo ({shortcuts.REDO.toString()})",
            icon_only=True,
        )
        self._redo_btn.clicked.connect(self._edit.redo)
        self._redo_btn.setEnabled(False)
        self._store.undo_stack.can_undo_changed.connect(self._undo_btn.setEnabled)
        self._store.undo_stack.can_redo_changed.connect(self._redo_btn.setEnabled)
        # Unsaved-dot wiring — UndoStack has no dirty_changed signal, so mark
        # dirty on every label mutation and clear it explicitly in _save_ass.
        self._store.labels_mutated.connect(
            lambda _ids: self._save_btn.set_unsaved(True)
        )
        self._store.labels_mutated.connect(self._on_labels_mutated_dirty)
        self._store.file_loaded.connect(self._on_file_loaded_dirty)
        self._main_tb.addWidget(self._undo_btn)
        self._main_tb.addWidget(self._redo_btn)

        self._main_tb.addSeparator()

        # Group navigation
        prev_group_btn = theme.IconButton(
            theme.Icons.prev_group(),
            tooltip=f"Previous group ({shortcuts.PREV_GROUP.toString()})",
            icon_only=True,
        )
        prev_group_btn.clicked.connect(lambda: self._goto_group(self._group_index - 1))
        next_group_btn = theme.IconButton(
            theme.Icons.next_group(),
            tooltip=f"Next group ({shortcuts.NEXT_GROUP.toString()})",
            icon_only=True,
        )
        next_group_btn.clicked.connect(lambda: self._goto_group(self._group_index + 1))
        self._main_tb.addWidget(prev_group_btn)
        self._main_tb.addWidget(next_group_btn)

        self._main_tb.addSeparator()

        # View toggles — order: file sidebar, gallery, labels list
        self._sidebar_btn = theme.IconButton(
            theme.Icons.sidebar_toggle(),
            tooltip="Toggle file sidebar",
            icon_only=True,
        )
        self._sidebar_btn.setCheckable(True)
        self._sidebar_btn.setChecked(self._app_settings.display.sidebar_visible)
        self._sidebar_btn.toggled.connect(self._on_sidebar_toggled)
        self._main_tb.addWidget(self._sidebar_btn)

        self._gallery_btn = theme.IconButton(
            theme.Icons.gallery_toggle(),
            tooltip=f"Toggle gallery ({shortcuts.TOGGLE_GALLERY.toString()})",
            icon_only=True,
        )
        self._gallery_btn.setCheckable(True)
        self._gallery_btn.setChecked(self._app_settings.display.gallery_visible)
        self._gallery_btn.toggled.connect(self._on_gallery_toggled)
        self._main_tb.addWidget(self._gallery_btn)

        self._labels_sidebar_btn = theme.IconButton(
            theme.Icons.labels_sidebar_toggle(),
            tooltip=f"Toggle labels list ({shortcuts.TOGGLE_LABELS_SIDEBAR.toString()})",
            icon_only=True,
        )
        self._labels_sidebar_btn.setCheckable(True)
        self._labels_sidebar_btn.setChecked(self._app_settings.display.labels_sidebar_visible)
        self._labels_sidebar_btn.toggled.connect(self._on_labels_sidebar_toggled)
        self._main_tb.addWidget(self._labels_sidebar_btn)

        # Stretch
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._main_tb.addWidget(spacer)

        # Settings cog
        self._settings_btn = theme.IconButton(
            theme.Icons.settings(), tooltip="Preferences", icon_only=True,
        )
        self._settings_btn.clicked.connect(self._show_settings_dialog)
        self._main_tb.addWidget(self._settings_btn)

        self._main_tb.hide()  # shown when editor page is active (existing behavior)

        # --- Status bar (Task 13) ---
        sb = self.statusBar()
        sb.setStyleSheet(
            f"QStatusBar {{ background: {theme.Tokens.bg_deepest}; "
            f"border-top: 1px solid {theme.Tokens.border}; }}"
            f"QStatusBar::item {{ border: none; }}"
        )

        self._sb_file = theme.StatusChip(theme.Icons.folder(), "—")
        self._sb_file_index = theme.StatusChip(theme.Icons.files(), "")
        self._sb_modified = theme.StatusChip(
            theme.Icons.modified_dot(color=theme.Tokens.alert),
            "unsaved", alert=True,
        )
        self._sb_time = theme.StatusChip(theme.Icons.time(), "—")
        self._sb_counts = theme.StatusChip(theme.Icons.style_tag(), "0 labels · 0 groups")
        self._sb_resolution = theme.StatusChip(theme.Icons.resolution(), "—")
        self._sb_hw = theme.StatusChip(theme.Icons.cpu(), "—")

        sb.addWidget(self._sb_file)
        sb.addWidget(self._sb_file_index)
        sb.addWidget(self._sb_modified)
        # Right side: addPermanentWidget pushes right
        sb.addPermanentWidget(self._sb_time)
        sb.addPermanentWidget(self._sb_counts)
        sb.addPermanentWidget(self._sb_resolution)
        sb.addPermanentWidget(self._sb_hw)

        # Initial visibility
        self._sb_file_index.hide()
        self._sb_modified.hide()

        # Wire signals
        self._store.labels_mutated.connect(self._on_labels_mutated_for_status)
        self._playback.time_changed.connect(self._on_time_changed_for_status)

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
        QShortcut(shortcuts.TOGGLE_GALLERY, self, lambda: self._gallery_btn.toggle())
        QShortcut(shortcuts.TOGGLE_LABELS_SIDEBAR, self,
                  lambda: self._labels_sidebar_btn.toggle())
        QShortcut(shortcuts.DUPLICATE, self, lambda: self._toolbar.duplicate_clicked.emit())
        QShortcut(shortcuts.PASTE_STYLE, self, lambda: self._toolbar.paste_style_clicked.emit())
        # Retiming shortcuts
        QShortcut(shortcuts.SET_IN, self).activated.connect(self._retime.set_in_at_current)
        QShortcut(shortcuts.SET_OUT, self).activated.connect(self._retime.set_out_at_current)
        QShortcut(shortcuts.NUDGE_BOTH_PREV, self).activated.connect(lambda: self._retime.nudge_both(-1))
        QShortcut(shortcuts.NUDGE_BOTH_NEXT, self).activated.connect(lambda: self._retime.nudge_both(+1))

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

        # Focused timeline signals
        self._focused_timeline.seeked.connect(self._on_focused_seeked)
        self._timeline.viewport_recenter_requested.connect(
            self._focused_timeline.recenter_to,
        )
        self._focused_timeline.view_changed.connect(self._timeline.set_viewport)

        # When the canvas geometry shifts, the floating label toolbar must
        # reposition against the new label rects. canvas_resized is emitted
        # from paintEvent AFTER _label_rects is refreshed, so the rects are
        # current by the time we read them here.
        self._player.canvas_resized.connect(self._update_toolbar_position)
        # The floating retime tray (RetimeBar + FocusedTimeline) overlays the
        # canvas; reposition it on any canvas resize, and again on selection
        # change so the first appearance lands at the right coordinates.
        self._player.canvas_resized.connect(self._layout_floating_retime_tray)
        self._store.selection_changed.connect(
            lambda _sel: self._layout_floating_retime_tray(),
        )

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
        self._welcome.clear_all_clicked.connect(self._on_clear_all_recent)

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
        if self._app_settings.display.labels_sidebar_visible:
            self._labels_dock.show()

    def _load_recent_dirs(self) -> None:
        from sub_label_pos.ui.recent_dirs import migrate
        raw = self._settings.value("recent_dirs", type=list) or []
        items = migrate(raw)
        # Drop entries pointing at folders that no longer exist
        items = [e for e in items if Path(e["path"]).is_dir()]
        self._welcome.set_recent_dirs(items)

    def _add_recent_dir(self, directory: str) -> None:
        from sub_label_pos.ui.recent_dirs import migrate, touch
        raw = self._settings.value("recent_dirs", type=list) or []
        items = migrate(raw)
        # Best-effort file count for the welcome card stats
        try:
            file_count = sum(
                1 for p in Path(directory).iterdir()
                if p.is_file() and p.suffix.lower() in {".mkv", ".mp4", ".avi", ".webm"}
            )
        except OSError:
            file_count = None
        items = touch(items, directory, file_count=file_count, max_items=self._MAX_RECENT)
        self._settings.setValue("recent_dirs", items)
        self._welcome.set_recent_dirs(items)

    def _open_recent_directory(self, folder: str) -> None:
        from sub_label_pos.ui.recent_dirs import migrate
        if not Path(folder).is_dir():
            raw = self._settings.value("recent_dirs", type=list) or []
            items = migrate(raw)
            items = [e for e in items if e["path"] != folder]
            self._settings.setValue("recent_dirs", items)
            self._welcome.set_recent_dirs(items)
            QMessageBox.warning(self, "Not Found", f"Directory no longer exists:\n{folder}")
            return
        self._switch_to_editor()
        self._open_folder_path(folder)

    def _on_clear_all_recent(self) -> None:
        self._settings.setValue("recent_dirs", [])
        self._welcome.set_recent_dirs([])

    # ── File loading ──

    def _open_video(self) -> None:
        self._subs_dir = None
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
        self._file_loader.load_video(Path(path), subs_dir=self._subs_dir)

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
        if hasattr(self, "_sb_file"):
            self._refresh_all_status_chips()

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
            self._push_playhead_to_focused(self._player._current_time)

    # ── Folder loading ──

    def _open_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Open Folder")
        if not folder:
            return
        self._switch_to_editor()
        self._open_folder_path(folder)

    def _open_folder_path(self, folder: str) -> None:
        self._cancel_folder_preload()
        self._subs_dir = None
        folder_path = Path(folder)

        def _scan(subs_dir: Path | None) -> list[str]:
            def _has_labels(video: Path) -> bool:
                ass_path = FileLoader.find_ass_sidecar(video, subs_dir=subs_dir)
                if ass_path is None:
                    return False
                try:
                    ass = AssFile(str(ass_path))
                    return bool(ass.labels)
                except Exception:
                    return False

            files = [
                str(p) for p in folder_path.iterdir()
                if p.is_file() and p.suffix.lower() in _VIDEO_EXTS
                and _has_labels(p)
            ]
            files.sort(key=_natural_sort_key)
            return files

        files = _scan(subs_dir=None)
        if not files:
            # No local sidecars — ask the user to point us at a subs folder.
            reply = QMessageBox.question(
                self,
                "No subtitle files found",
                (
                    f"No .ass subtitle files were found next to the videos in "
                    f"{folder_path}.\n\nPick a folder where the subtitle files live?"
                ),
                QMessageBox.StandardButton.Open | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Open,
            )
            if reply != QMessageBox.StandardButton.Open:
                return
            subs_choice = QFileDialog.getExistingDirectory(
                self, "Select subtitle folder"
            )
            if not subs_choice:
                return
            self._subs_dir = Path(subs_choice)
            files = _scan(subs_dir=self._subs_dir)
            if not files:
                QMessageBox.information(
                    self,
                    "No Videos",
                    "No video files with matching .ass subtitle files containing labels found in the selected folder.",
                )
                self._subs_dir = None
                return

        self._folder_files = files
        # Populate sidebar
        self._folder_path_label.setText(folder)
        self._file_list.blockSignals(True)
        self._file_list.clear()
        self._file_row_widgets = {}
        for f in files:
            row = _FileRowWidget(Path(f).name)
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, 26))
            item.setData(Qt.ItemDataRole.UserRole, f)
            self._file_list.addItem(item)
            self._file_list.setItemWidget(item, row)
            self._file_row_widgets[f] = row
        self._file_list.blockSignals(False)
        self._files_dock.show()
        # Show progress bar at 0% before preload starts
        if self._folder_files:
            self._preload_bar.setRange(0, len(self._folder_files))
            self._preload_bar.setValue(0)
            self._preload_bar.show()
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
        # Re-centre the preload ring on the new active file so neighbours
        # get queued ahead of distant files. This applies whether the
        # current file is a cache hit or a cache miss.
        if self._preload_worker is not None:
            self._preload_worker.set_active_index(index)
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
        self._preload_worker = FolderPreloadWorker(
            file_paths,
            self._video_service,
            workers=self._app_settings.perf.preload_workers,
            ring=self._app_settings.perf.preload_ring,
            generate_thumbnails=self._should_generate_gallery(),
            subs_dir=self._subs_dir,
        )
        # Centre the ring on the currently-active file in the sidebar so
        # the first file the user opens gets preloaded first, ahead of
        # its neighbours.
        if self._folder_index >= 0:
            self._preload_worker.set_active_index(self._folder_index)
        self._preload_worker.file_ready.connect(self._on_file_preloaded)
        self._preload_worker.all_done.connect(self._on_preload_done)
        self._preload_worker.start()

    def _on_file_preloaded(self, path: str, data: object) -> None:
        if not isinstance(data, PreloadedFileData):
            return
        self._preloaded[path] = data
        # Mark status dot READY
        row = self._file_row_widgets.get(path)
        if row is not None:
            row.set_state(_FileRowWidget.READY)
        # Bump progress bar
        if self._folder_files:
            ready = sum(
                1 for r in self._file_row_widgets.values()
                if r._state == _FileRowWidget.READY
            )
            total = len(self._folder_files)
            self._preload_bar.setRange(0, total)
            self._preload_bar.setValue(ready)
            self._preload_bar.show()

    def _on_preload_done(self) -> None:
        _status_msg(self, "Folder pre-loading complete")
        self._preload_bar.hide()

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
            if self._should_generate_gallery():
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
        if hasattr(self, "_sb_file"):
            self._refresh_all_status_chips()

    def _open_ass(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open ASS", "", "ASS Subtitles (*.ass);;All (*)"
        )
        if path:
            self._apply_loaded_ass(AssFile(path), path)

    # ── Status bar helpers (Task 13) ──

    def _on_labels_mutated_for_status(self, _ids) -> None:
        self._sb_modified.show()
        self._update_status_counts()

    def _on_time_changed_for_status(self, current_s: float) -> None:
        self._sb_time.set_text(_format_hms(current_s))

    def _update_status_file(self) -> None:
        if not self._video_path:
            self._sb_file.set_text("—")
            return
        self._sb_file.set_text(Path(self._video_path).name)

    def _update_status_counts(self) -> None:
        n_labels = len(self._store.state.labels) if self._store.state else 0
        n_groups = len(self._groups) if hasattr(self, "_groups") else 0
        self._sb_counts.set_text(f"{n_labels} labels · {n_groups} groups")
        if hasattr(self, "_gallery_handle"):
            self._gallery_handle.set_counts(n_groups, n_labels)

    def _should_generate_gallery(self) -> bool:
        """Return False when the gallery is hidden on a Performance-tier
        machine, to skip all thumbnail extraction / caching work entirely.

        On Balanced/Quality tiers we keep the existing behavior (still
        precompute thumbs so toggling the gallery on is instant). Only the
        low tier opts out of work for a hidden gallery.
        """
        if self._app_settings.hardware_tier != "low":
            return True
        return self._app_settings.display.gallery_visible

    def _update_status_resolution(self) -> None:
        if self._ass is not None and hasattr(self._ass, "play_res_x"):
            self._sb_resolution.set_text(
                f"{self._ass.play_res_x}×{self._ass.play_res_y}"
            )
        else:
            self._sb_resolution.set_text("—")

    def _update_status_hw(self) -> None:
        hw = self._init_settings.value("mpv/hwdec", "auto-safe")
        q = "HQ" if self._app_settings.perf.mpv_quality == "high" else "SQ"
        self._sb_hw.set_text(f"{hw} · {q}")

    def _update_status_file_index(self) -> None:
        """Show 'N / M' chip when in folder mode, hide otherwise."""
        files = getattr(self, "_folder_files", None) or []
        if files and self._video_path:
            try:
                idx = files.index(self._video_path)
                self._sb_file_index.set_text(f"{idx + 1} / {len(files)}")
                self._sb_file_index.show()
                return
            except ValueError:
                pass
        self._sb_file_index.hide()

    def _refresh_all_status_chips(self) -> None:
        """Call after a successful file load."""
        self._update_status_file()
        self._update_status_counts()
        self._update_status_resolution()
        self._update_status_hw()
        self._update_status_file_index()

    def _on_redetect_hardware(self) -> None:
        """Re-run hardware detection and rewrite the persisted defaults.

        Most perf knobs (FrameCache size, FrameRequestQueue workers,
        thumbnail max-dim/quality, FolderPreloadWorker concurrency) are
        applied at MainWindow construction time, so a restart is required
        for the new values to take effect.
        """
        from sub_label_pos.services.app_settings import TIER_LABELS
        new_settings = redetect_settings()
        old = self._app_settings
        new = new_settings
        old_label = TIER_LABELS.get(old.hardware_tier, old.hardware_tier)
        new_label = TIER_LABELS.get(new.hardware_tier, new.hardware_tier)
        msg = (
            f"Detected: {new.detected_ram_gb:.1f} GB RAM, "
            f"{new.detected_cpu_cores} cores\n"
            f"Profile: {old_label} → {new_label}\n\n"
            f"thumb max dim: {old.perf.thumb_max_dim} → {new.perf.thumb_max_dim}\n"
            f"frame cache size: {old.perf.frame_cache_size} → {new.perf.frame_cache_size}\n"
            f"preload workers: {old.perf.preload_workers} → {new.perf.preload_workers}\n"
            f"frame queue workers: {old.perf.frame_queue_workers} → {new.perf.frame_queue_workers}\n\n"
            "Restart the app for the new values to take effect."
        )
        QMessageBox.information(self, "Hardware re-detected", msg)
        self._app_settings = new_settings

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
        # Clear the unsaved-dot on the primary Save button (Task 12)
        if hasattr(self, "_save_btn"):
            self._save_btn.set_unsaved(False)
        self._dirty_label_ids.clear()
        if hasattr(self, "_labels_sidebar"):
            self._labels_sidebar.mark_clean()
        # Hide the unsaved chip in the status bar (Task 13)
        if hasattr(self, "_sb_modified"):
            self._sb_modified.hide()

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
        self._push_playhead_to_focused(group.representative_time)
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
            self._push_playhead_to_focused(t)
            self._player.prefetch_around(t)
            self._timeline.set_time(t)

    # ── Responsive resize (Task 13) ──

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if not hasattr(self, "_sb_hw"):
            return  # too early in init
        w = self.width()
        self._sb_hw.setVisible(w >= 1100)
        self._sb_resolution.setVisible(w >= 950)
        self._sb_counts.setVisible(w >= 800)

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
        self._subs_dir = None
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

    def _layout_floating_retime_tray(self) -> None:
        """Position the floating RetimeBar + FocusedTimeline tray at the bottom
        of the canvas. The tray overlays the canvas so selection toggles do
        not displace the timeline below.

        Called on canvas resize and on selection change (the latter so the
        widgets are positioned the first time they become visible)."""
        if not hasattr(self, "_focused_timeline") or not hasattr(self, "_retime_bar"):
            return
        canvas_w = self._player.width()
        canvas_h = self._player.height()
        # Resize tray children to span the canvas width.
        rb_h = self._retime_bar.sizeHint().height()
        ft_h = self._focused_timeline.sizeHint().height()
        self._retime_bar.resize(canvas_w, rb_h)
        self._focused_timeline.resize(canvas_w, ft_h)
        # Stack from bottom up: focused timeline flush with bottom, retime bar above.
        ft_y = canvas_h - ft_h
        rb_y = ft_y - rb_h
        self._retime_bar.move(0, rb_y)
        self._focused_timeline.move(0, ft_y)
        self._retime_bar.raise_()
        self._focused_timeline.raise_()

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
        self._push_playhead_to_focused(self._player._current_time)
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
        self._push_playhead_to_focused(self._player._current_time)
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
                self._push_playhead_to_focused(self._player._current_time)
            else:
                next_gi = min(gi, len(self._groups) - 1)
                self._goto_group(next_gi)
        else:
            self._player.show_time(self._player._current_time)
            self._push_playhead_to_focused(self._player._current_time)
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
            self._push_playhead_to_focused(self._player._current_time)
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
        self._player.clear_font_corrections()
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
        self._player.clear_font_corrections()
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
        self._push_playhead_to_focused(self._player._current_time)
        _status_msg(self, f"Updated text to \"{label.text}\"")

    # ── Context menus ──

    def _on_context_menu(self, pos: QPointF) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background: {theme.Tokens.bg_raised}; "
            f"color: {theme.Tokens.text_primary}; "
            f"border: 1px solid {theme.Tokens.border_strong}; "
            f"border-radius: 6px; padding: 4px 0; font-size: 11.5px; }}"
            f"QMenu::item {{ padding: 5px 28px 5px 12px; }}"
            f"QMenu::item:selected {{ background: {theme.Tokens.bg_hover}; "
            f"color: {theme.Tokens.text_emphasis}; }}"
            f"QMenu::separator {{ height: 1px; background: {theme.Tokens.border}; "
            f"margin: 4px 0; }}"
        )

        selected = self._player.selected_labels()
        single = len(selected) == 1

        if single:
            act = menu.addAction(theme.Icons.edit_text(), "Edit text")
            act.triggered.connect(lambda: self._on_edit_requested(selected[0]))

            act = menu.addAction(theme.Icons.duplicate(), "Duplicate")
            act.setShortcut(shortcuts.DUPLICATE)
            act.triggered.connect(self._on_duplicate)

            menu.addSeparator()

            act = menu.addAction(theme.Icons.copy_style(), "Copy style…")
            act.triggered.connect(self._on_copy_style)

        if self._style_clipboard is not None:
            act = menu.addAction(theme.Icons.paste_style(), "Paste style")
            act.setShortcut(shortcuts.PASTE_STYLE)
            act.triggered.connect(self._on_paste_style)

        if len(selected) >= 2:
            menu.addSeparator()
            act = menu.addAction(theme.Icons.sync_times(), "Sync times")
            act.triggered.connect(lambda: self._on_sync_times(selected))

        if 2 <= len(selected) <= 3:
            self._build_merge_submenu(menu, selected)

        menu.addSeparator()
        act = menu.addAction(theme.Icons.delete(color=theme.Tokens.danger), "Delete")
        act.setShortcut(shortcuts.DELETE_SELECTED)
        act.triggered.connect(self._on_delete)

        global_pos = self._player.mapToGlobal(pos.toPoint())
        menu.exec(global_pos)

    def _on_gallery_context_menu(self, index: int) -> None:
        if not (0 <= index < len(self._groups)):
            return
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background: {theme.Tokens.bg_raised}; "
            f"color: {theme.Tokens.text_primary}; "
            f"border: 1px solid {theme.Tokens.border_strong}; "
            f"border-radius: 6px; padding: 4px 0; font-size: 11.5px; }}"
            f"QMenu::item {{ padding: 5px 28px 5px 12px; }}"
            f"QMenu::item:selected {{ background: {theme.Tokens.bg_hover}; "
            f"color: {theme.Tokens.text_emphasis}; }}"
        )
        act = menu.addAction(theme.Icons.delete(color=theme.Tokens.danger), "Delete")
        act.triggered.connect(lambda: self._delete_group(index))
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
        menu.setStyleSheet(
            f"QMenu {{ background: {theme.Tokens.bg_raised}; "
            f"color: {theme.Tokens.text_primary}; "
            f"border: 1px solid {theme.Tokens.border_strong}; "
            f"border-radius: 6px; padding: 4px 0; font-size: 11.5px; }}"
            f"QMenu::item {{ padding: 5px 28px 5px 12px; }}"
            f"QMenu::item:selected {{ background: {theme.Tokens.bg_hover}; "
            f"color: {theme.Tokens.text_emphasis}; }}"
        )
        act = menu.addAction(theme.Icons.duplicate(), "Create label")
        act.triggered.connect(lambda: self._create_label_at(pos))
        if self._style_clipboard is not None:
            act = menu.addAction(theme.Icons.paste_style(), "Paste style")
            act.setShortcut(shortcuts.PASTE_STYLE)
            act.triggered.connect(self._on_paste_style)
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
        self._push_playhead_to_focused(self._player._current_time)
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
        self._push_playhead_to_focused(seconds)

    def _on_focused_seeked(self, seconds: float) -> None:
        """User clicked on empty area of the focused strip — seek the player."""
        self._seek_player_to(seconds)

    def _seek_player_to(self, seconds: float) -> None:
        """Seek the editor canvas + main timeline + focused strip to a given
        time. Used by the focused-strip click handler and by RetimeController
        (e.g. seek_to_in / seek_to_out)."""
        self._player.show_time(seconds)
        if hasattr(self, "_timeline"):
            self._timeline.set_time(seconds)
        self._push_playhead_to_focused(seconds)

    def _push_playhead_to_focused(self, seconds: float) -> None:
        """Push the playhead position into the focused timeline strip."""
        if hasattr(self, "_focused_timeline"):
            self._focused_timeline.set_playhead(seconds)

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
        # Legacy slot — the combobox no longer exists. Kept as a no-op stub so
        # any remaining internal callers don't crash during the transition.
        pass

    def _on_hwdec_changed_by_value(self, value: str) -> None:
        """Apply hwdec change from the settings dialog (replaces combobox-driven path)."""
        self._mpv_widget.set_hwdec(value)
        self._settings.setValue("mpv/hwdec", value)

    def _on_hq_toggled(self, checked: bool) -> None:
        self._mpv_widget.set_high_quality(checked)
        # Persist into PerfSettings so the next launch remembers regardless
        # of hardware tier defaults.
        self._app_settings.perf.mpv_quality = "high" if checked else "low"
        try:
            save_perf_settings(self._app_settings.perf)
        except Exception:
            log.exception("Failed to persist mpv_quality setting")

    def _on_gallery_handle_toggled(self, expanded: bool) -> None:
        # Delegate to the canonical toggler so handle/toolbar/dialog all
        # resize the splitter and sync each other.
        self._on_gallery_toggled(expanded)

    def _on_show_gallery_toggled(self, checked: bool) -> None:
        """Legacy slot — delegates to the new _on_gallery_toggled."""
        self._on_gallery_toggled(checked)

    def _on_gallery_toggled(self, visible: bool) -> None:
        """Toggle the gallery panel's visibility and persist the choice.

        When the gallery becomes visible its own ``showEvent`` triggers a
        one-time thumbnail rebuild so it picks up the current store state.
        When hidden, ``_start_thumbnail_loading``/``_refresh_single_thumbnail``
        no-op so no ffmpeg/CPU work is spent rendering invisible thumbs.
        """
        self._gallery.setVisible(visible)
        self._resize_gallery_container(visible)
        self._app_settings.display.gallery_visible = visible
        from sub_label_pos.services.app_settings import save_display
        save_display(self._app_settings.display)
        if hasattr(self, "_gallery_btn") and self._gallery_btn.isChecked() != visible:
            self._gallery_btn.blockSignals(True)
            self._gallery_btn.setChecked(visible)
            self._gallery_btn.blockSignals(False)
        if hasattr(self, "_gallery_handle") and self._gallery_handle._expanded != visible:
            self._gallery_handle.set_expanded(visible)

    def _resize_gallery_container(self, expanded: bool) -> None:
        """Shrink the gallery container to the handle height when collapsed.

        Without this, the splitter keeps the container at its prior expanded
        size and the handle floats mid-screen with empty space below it.
        """
        if not hasattr(self, "_splitter"):
            return
        sizes = self._splitter.sizes()
        if len(sizes) < 2:
            return
        # Use the splitter's actual height as the source of truth — sizes()
        # can be [0, 0] before the first layout pass on first show.
        total = self._splitter.height() or sum(sizes) or 720
        handle_h = max(24, self._gallery_handle.sizeHint().height())
        if expanded:
            target = max(160, sizes[1] if sizes[1] > handle_h else 0, 160)
            target = min(target, max(handle_h, total - 100))
        else:
            target = handle_h
        self._splitter.setSizes([max(0, total - target), target])

    def _on_sidebar_toggled(self, visible: bool) -> None:
        """Toggle the file sidebar dock visibility and persist the choice."""
        self._files_dock.setVisible(visible)
        self._app_settings.display.sidebar_visible = visible
        from sub_label_pos.services.app_settings import save_display
        save_display(self._app_settings.display)

    def _on_labels_sidebar_toggled(self, visible: bool) -> None:
        """Toggle the labels-sidebar dock visibility and persist the choice."""
        self._labels_dock.setVisible(visible)
        self._app_settings.display.labels_sidebar_visible = visible
        from sub_label_pos.services.app_settings import save_display
        save_display(self._app_settings.display)
        if self._labels_sidebar_btn.isChecked() != visible:
            self._labels_sidebar_btn.blockSignals(True)
            self._labels_sidebar_btn.setChecked(visible)
            self._labels_sidebar_btn.blockSignals(False)

    def _on_labels_mutated_dirty(self, ids: set) -> None:
        self._dirty_label_ids |= set(ids)
        if hasattr(self, "_labels_sidebar"):
            self._labels_sidebar.set_dirty_ids(self._dirty_label_ids)

    def _on_file_loaded_dirty(self, _path) -> None:
        self._dirty_label_ids.clear()
        if hasattr(self, "_labels_sidebar"):
            self._labels_sidebar.set_dirty_ids(self._dirty_label_ids)

    def _on_labels_row_clicked(self, row) -> None:
        """Sidebar row left-clicked: seek to start AND select first label."""
        self._on_labels_row_jump(row)
        first_id = getattr(row.labels[0], "label_id", None)
        if first_id is not None:
            self._store.set_selection({first_id})

    def _on_labels_row_jump(self, row) -> None:
        """Sidebar row jump: seek to start, without changing selection."""
        if hasattr(self._playback, "seek_to_time"):
            self._playback.seek_to_time(row.start_time)
        else:
            # Fallback: drive the player widget directly (always present).
            self._player.show_time(row.start_time)
            self._push_playhead_to_focused(row.start_time)

    def _delete_labels(self, labels) -> None:
        """Delete the given iterable of LabelDialogue objects.

        Factored from _on_delete (which still operates on canvas selection)
        so the labels sidebar can delete a whole group at once.
        """
        ids = [lb.label_id for lb in labels if getattr(lb, "label_id", None) is not None]
        if not ids:
            return
        self._edit.delete(ids)

    def _show_settings_dialog(self) -> None:
        """Open the Settings dialog. Applies changes on OK."""
        dialog = SettingsDialog(self._app_settings, parent=self)
        saved_hwdec = self._init_settings.value("mpv/hwdec", "auto-safe")
        dialog.set_initial_hwdec(saved_hwdec)
        dialog.hardware_redetect_requested.connect(self._on_redetect_hardware)
        dialog.tier_overridden.connect(self._on_tier_overridden)
        if dialog.exec():
            new_hwdec = dialog.selected_hwdec()
            self._init_settings.setValue("mpv/hwdec", new_hwdec)
            self._on_hwdec_changed_by_value(new_hwdec)
            self._on_hq_toggled(self._app_settings.perf.mpv_quality == "high")
            self._on_gallery_toggled(self._app_settings.display.gallery_visible)
            self._on_sidebar_toggled(self._app_settings.display.sidebar_visible)
            self._on_labels_sidebar_toggled(self._app_settings.display.labels_sidebar_visible)
            self._update_status_hw()

    def _on_tier_overridden(self, new_tier: str) -> None:
        """User explicitly picked a different performance profile in the
        Settings dialog. Most perf knobs (cache size, worker counts, thumb
        max-dim) take effect only on next launch — inform the user.
        """
        from sub_label_pos.services.app_settings import TIER_LABELS
        QMessageBox.information(
            self,
            "Performance profile changed",
            f"Profile set to <b>{TIER_LABELS.get(new_tier, new_tier)}</b>.\n\n"
            "Restart the app for cache size, worker count, and thumbnail "
            "settings to take effect.",
        )

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
        self._gallery.shutdown()
        self._mpv_widget.shutdown()
        self._player.shutdown()
        self._frame_queue.shutdown()
        super().closeEvent(event)
