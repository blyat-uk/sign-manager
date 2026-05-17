"""PlaybackOrchestrator — manages edit/playback mode switching and time sync.

Extracted from MainWindow as part of refactor task K3. MainWindow still owns
the construction of the mpv widget, the video stack, and the timeline; the
orchestrator only owns the *transitions* between the two modes and the bits
of state that drive them (current playback mode, timeline sync on
``time-pos`` updates, etc.). This keeps the mpv widget lifecycle in
MainWindow (where it is intertwined with file loading) but pulls the
mode-switch decision tree out of MainWindow.

Higher-level concerns left in MainWindow:
  * Building the mpv widget, the video stack, and the toolbar.
  * Picking the "start time" for a playback session (depends on the
    currently selected group and the ``_playback_from_group`` flag, both
    of which are owned by MainWindow). MainWindow injects this via the
    ``start_time_provider`` callable.
  * Reacting to mode changes for UI side-effects (e.g. hiding the floating
    label toolbar). MainWindow listens on the ``mode_changed`` signal.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Literal

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QImage

if TYPE_CHECKING:
    from PyQt6.QtWidgets import QStackedWidget

    from sub_label_pos.services.mpv_service import MpvPreviewWidget
    from sub_label_pos.ui.timeline_widget import TimelineWidget
    from sub_label_pos.ui.video_widget import VideoFrameWidget

log = logging.getLogger(__name__)

Mode = Literal["edit", "playback"]


class PlaybackOrchestrator(QObject):
    """Coordinates edit/playback mode + timeline ↔ mpv time synchronisation.

    Signals:
      mode_changed(str): "edit" or "playback" — emitted whenever the mode
        actually transitions (no-ops do not emit).
      time_changed(float): the current playback time in seconds — emitted
        whenever the mpv widget reports a new time-pos *and* we are in
        playback mode. Edit-mode time changes are not emitted here because
        the editor view (``VideoFrameWidget``) already owns its current
        time and re-renders directly.
    """

    mode_changed = pyqtSignal(str)
    time_changed = pyqtSignal(float)

    # Stack indices in MainWindow's ``_video_stack``.
    _MPV_INDEX = 0
    _EDITOR_INDEX = 1

    def __init__(self) -> None:
        super().__init__()
        self._mode: Mode = "edit"

        # Widget references injected via setters — MainWindow constructs
        # these in a specific order during its own ``__init__`` and we
        # don't want to mandate construction order via ctor args.
        self._mpv: MpvPreviewWidget | None = None
        self._editor: VideoFrameWidget | None = None
        self._timeline: TimelineWidget | None = None
        self._stack: QStackedWidget | None = None

        # Provided by MainWindow: returns the time we should start playback
        # at (e.g. the start of the currently selected group). If unset we
        # fall back to the editor's current_time.
        self._start_time_provider: Callable[[], float] | None = None

        # Guards: MainWindow sets these via ``set_video_loaded`` /
        # ``set_subtitles_loaded`` so the orchestrator can refuse to enter
        # playback when nothing is loaded and can re-attach subtitles after
        # an mpv ``file_loaded`` event.
        self._video_loaded: bool = False
        self._ass_path: str | None = None

    # --- Widget injection ----------------------------------------------

    def attach_mpv_widget(self, w: "MpvPreviewWidget") -> None:
        self._mpv = w

    def attach_editor_widget(self, w: "VideoFrameWidget") -> None:
        self._editor = w

    def attach_timeline_widget(self, w: "TimelineWidget") -> None:
        self._timeline = w

    def attach_video_stack(self, s: "QStackedWidget") -> None:
        self._stack = s

    def set_start_time_provider(self, fn: Callable[[], float]) -> None:
        self._start_time_provider = fn

    # --- Loaded-resource notifications ---------------------------------

    def set_video_loaded(self, loaded: bool) -> None:
        """Tell the orchestrator whether a video is currently loaded."""
        self._video_loaded = loaded
        if not loaded:
            # If a video unloads while in playback mode, fall back to edit.
            if self._mode == "playback":
                self._mode = "edit"
                self.mode_changed.emit("edit")

    def set_subtitles_path(self, ass_path: str | None) -> None:
        """Remember the current ASS path so we can re-load it on mpv
        ``file_loaded`` events (the deferred-playback flow)."""
        self._ass_path = ass_path

    # --- Mode ----------------------------------------------------------

    @property
    def mode(self) -> Mode:
        return self._mode

    @property
    def is_playback(self) -> bool:
        return self._mode == "playback"

    @property
    def is_edit(self) -> bool:
        return self._mode == "edit"

    def _start_time(self) -> float:
        if self._start_time_provider is not None:
            try:
                return float(self._start_time_provider())
            except Exception as e:  # pragma: no cover — defensive
                log.warning("start_time_provider failed: %s", e)
        if self._editor is not None:
            return float(getattr(self._editor, "_current_time", 0.0))
        return 0.0

    def enter_playback_mode(self) -> None:
        """Switch to mpv playback mode."""
        if self._mode == "playback":
            return
        if not self._video_loaded:
            return
        self._mode = "playback"
        if self._stack is not None:
            self._stack.setCurrentIndex(self._MPV_INDEX)

        if self._mpv is not None:
            if self._mpv.is_file_loaded:
                start = self._start_time()
                self._mpv.seek_absolute(start)
                self._mpv.play()
            else:
                # File not yet loaded — defer start until mpv signals
                # file_loaded. We use SingleShotConnection so we don't
                # accumulate stale handlers across re-loads.
                self._mpv.file_loaded.connect(
                    self._on_mpv_file_loaded_for_playback,
                    Qt.ConnectionType.SingleShotConnection,
                )
        if self._timeline is not None:
            self._timeline.set_playing(True)
        self.mode_changed.emit("playback")

    def _on_mpv_file_loaded_for_playback(self) -> None:
        """Slot for the deferred-playback flow described in
        ``enter_playback_mode``."""
        if self._mode != "playback" or self._mpv is None:
            return
        start = self._start_time()
        self._mpv.seek_absolute(start)
        self._mpv.play()
        if self._ass_path:
            self._mpv.load_subtitles(self._ass_path)

    def enter_edit_mode(self, capture: bool = True) -> None:
        """Switch to QPainter edit mode.

        If ``capture`` is true and we have an mpv widget, capture the
        currently-displayed mpv frame so the editor doesn't have to fall
        back to ffmpeg for the same timestamp."""
        if self._mode == "edit":
            return
        time_pos: float = 0.0
        if self._mpv is not None:
            self._mpv.pause()
            time_pos = self._mpv.time_pos
        self._mode = "edit"

        # Set the editor's current time *before* show_frame_from_image so
        # the captured frame is cached under the correct timestamp rather
        # than the previous representative time.
        if self._editor is not None:
            self._editor._current_time = time_pos

        if capture and self._mpv is not None and self._editor is not None:
            frame_data = self._mpv.capture_frame()
            if frame_data:
                rgb_bytes, w, h = frame_data
                img = QImage(rgb_bytes, w, h, w * 3, QImage.Format.Format_RGB888)
                # QImage doesn't copy the data, so .copy() ensures it's owned
                img = img.copy()
                self._editor.show_frame_from_image(img)
        if self._editor is not None:
            self._editor._update_visible_labels(time_pos)
            self._editor._update_scaled_pixmap()
            self._editor.update()

        if self._stack is not None:
            self._stack.setCurrentIndex(self._EDITOR_INDEX)
        if self._timeline is not None:
            self._timeline.set_playing(False)
            self._timeline.set_time(time_pos)
        self.mode_changed.emit("edit")

    def toggle(self) -> None:
        """Toggle between playback and edit modes."""
        if self._mode == "playback":
            self.enter_edit_mode()
        else:
            self.enter_playback_mode()

    # --- mpv → timeline sync ------------------------------------------

    def on_mpv_time_pos(self, seconds: float) -> None:
        """Forward an mpv time-pos update to the timeline (only while we
        are actually in playback mode)."""
        if self._mode == "playback" and self._timeline is not None:
            self._timeline.set_time(seconds)
        # Always emit time_changed so listeners that care about both
        # modes (none yet, but kept for symmetry with the test surface)
        # can observe it.
        self.time_changed.emit(seconds)

    def on_mpv_eof(self) -> None:
        """Mpv reached end-of-file: snap back to edit mode without trying
        to capture the (likely-black) last frame."""
        if self._mode == "playback":
            self.enter_edit_mode(capture=False)

    # --- Reset ---------------------------------------------------------

    def reset_to_edit(self) -> None:
        """Force the orchestrator back to edit mode without touching mpv.

        Used by MainWindow when a new file is loaded — the mpv widget has
        already been reset by ``load(...)``, so we just need to update our
        own state + the timeline."""
        self._mode = "edit"
        if self._stack is not None:
            self._stack.setCurrentIndex(self._EDITOR_INDEX)
        if self._timeline is not None:
            self._timeline.set_playing(False)
        self.mode_changed.emit("edit")
