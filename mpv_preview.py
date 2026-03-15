"""MpvPreviewWidget — embeds mpv via OpenGL render context for video playback."""
from __future__ import annotations

import locale

from PyQt6.QtCore import Qt, QMetaObject, pyqtSignal, pyqtSlot, Q_ARG
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtWidgets import QWidget

import mpv


def _get_process_address(_ctx, name):
    """Callback for mpv to resolve OpenGL function addresses."""
    from PyQt6.QtGui import QOpenGLContext
    gl_ctx = QOpenGLContext.currentContext()
    if gl_ctx is None:
        return 0
    addr = gl_ctx.getProcAddress(name)
    if addr is None:
        return 0
    return int(addr)


_proc_address_fn = mpv.MpvGlGetProcAddressFn(_get_process_address)


class MpvPreviewWidget(QOpenGLWidget):
    """Video playback widget using mpv's OpenGL render API.

    Signals:
        time_pos_changed(float): emitted when playback position changes
        duration_changed(float): emitted when video duration is known
        pause_changed(bool): emitted when pause state changes
        file_loaded(): emitted when a file has been loaded and is ready
        eof_reached(): emitted when playback reaches end of file
    """

    time_pos_changed = pyqtSignal(float)
    duration_changed = pyqtSignal(float)
    pause_changed = pyqtSignal(bool)
    file_loaded = pyqtSignal()
    eof_reached = pyqtSignal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._mpv: mpv.MPV | None = None
        self._ctx: mpv.MpvRenderContext | None = None
        self._video_path: str | None = None
        self._ass_path: str | None = None
        self._shutting_down = False
        self._file_loaded_flag = False

        # Ensure locale is set for mpv
        locale.setlocale(locale.LC_NUMERIC, "C")

    def initializeGL(self) -> None:
        self._mpv = mpv.MPV(
            vo="libmpv",
            keep_open="yes",
            idle="yes",
            osd_level=0,
            sub_auto="no",
            hwdec="auto-safe",
            input_default_bindings="no",
            input_vo_keyboard="no",
        )

        # Create render context with OpenGL backend
        self._ctx = mpv.MpvRenderContext(
            self._mpv,
            "opengl",
            opengl_init_params={
                "get_proc_address": _proc_address_fn,
            },
        )

        # When mpv has a new frame, schedule a repaint on the Qt main thread
        self._ctx.update_cb = self._on_mpv_update

        # Observe properties
        self._mpv.observe_property("time-pos", self._on_time_pos)
        self._mpv.observe_property("duration", self._on_duration)
        self._mpv.observe_property("pause", self._on_pause)
        self._mpv.observe_property("eof-reached", self._on_eof)

        # File loaded event
        @self._mpv.event_callback("file-loaded")
        def _on_file_loaded(event):
            self._on_file_loaded_event(event)
        self._file_loaded_cb = _on_file_loaded  # prevent GC

        # If a video was requested before GL init, load it now
        if self._video_path:
            self._do_load()

    def paintGL(self) -> None:
        if self._ctx is None:
            return

        # Get the default framebuffer object for this widget
        fbo = self.defaultFramebufferObject()
        ratio = self.devicePixelRatioF()
        w = int(self.width() * ratio)
        h = int(self.height() * ratio)

        self._ctx.render(
            flip_y=True,
            opengl_fbo={
                "fbo": fbo,
                "w": w,
                "h": h,
            },
        )

    def _on_mpv_update(self) -> None:
        """Called from mpv's render thread — marshal to Qt main thread."""
        if self._shutting_down:
            return
        QMetaObject.invokeMethod(
            self, "_do_update",
            Qt.ConnectionType.QueuedConnection,
        )

    @pyqtSlot()
    def _do_update(self) -> None:
        """Slot invoked on the Qt main thread to trigger repaint."""
        if self._ctx and not self._shutting_down:
            self.update()

    # ── Property observers (called from mpv thread) ──

    def _on_time_pos(self, _name: str, value: float | None) -> None:
        if value is not None and not self._shutting_down:
            QMetaObject.invokeMethod(
                self, "_emit_time_pos",
                Qt.ConnectionType.QueuedConnection,
                Q_ARG(float, value),
            )

    @pyqtSlot(float)
    def _emit_time_pos(self, value: float) -> None:
        self.time_pos_changed.emit(value)

    def _on_duration(self, _name: str, value: float | None) -> None:
        if value is not None and not self._shutting_down:
            QMetaObject.invokeMethod(
                self, "_emit_duration",
                Qt.ConnectionType.QueuedConnection,
                Q_ARG(float, value),
            )

    @pyqtSlot(float)
    def _emit_duration(self, value: float) -> None:
        self.duration_changed.emit(value)

    def _on_pause(self, _name: str, value: bool | None) -> None:
        if value is not None and not self._shutting_down:
            QMetaObject.invokeMethod(
                self, "_emit_pause",
                Qt.ConnectionType.QueuedConnection,
                Q_ARG(bool, value),
            )

    @pyqtSlot(bool)
    def _emit_pause(self, value: bool) -> None:
        self.pause_changed.emit(value)

    def _on_eof(self, _name: str, value: bool | None) -> None:
        if value and not self._shutting_down:
            QMetaObject.invokeMethod(
                self, "_emit_eof",
                Qt.ConnectionType.QueuedConnection,
            )

    @pyqtSlot()
    def _emit_eof(self) -> None:
        self.eof_reached.emit()

    # ── Public API ──

    def load(self, video_path: str) -> None:
        """Load a video file. If GL is not yet initialised, defers the load."""
        self._video_path = video_path
        if self._mpv is not None:
            self._do_load()

    def _do_load(self) -> None:
        if not self._mpv or not self._video_path:
            return
        self._file_loaded_flag = False
        self._mpv.command("loadfile", self._video_path)
        self._mpv.pause = True

    def _on_file_loaded_event(self, event) -> None:
        if not self._shutting_down:
            self._file_loaded_flag = True
            # Re-add subtitles after file load (mpv resets tracks on new file)
            if self._ass_path:
                try:
                    self._mpv.command("sub-add", self._ass_path, "select")
                except Exception:
                    pass
            QMetaObject.invokeMethod(
                self, "_emit_file_loaded",
                Qt.ConnectionType.QueuedConnection,
            )

    @pyqtSlot()
    def _emit_file_loaded(self) -> None:
        self.file_loaded.emit()

    def load_subtitles(self, ass_path: str) -> None:
        """Add an ASS subtitle track for playback preview."""
        self._ass_path = ass_path
        if self._mpv is not None and self._file_loaded_flag:
            try:
                self._mpv.command("sub-remove")
            except Exception:
                pass
            try:
                self._mpv.command("sub-add", ass_path, "select")
            except Exception:
                pass

    def reload_subtitles(self) -> None:
        """Reload the subtitle file after edits."""
        if self._ass_path and self._mpv is not None:
            try:
                self._mpv.command("sub-reload")
            except Exception:
                # Fallback: remove + re-add
                try:
                    self._mpv.command("sub-remove")
                except Exception:
                    pass
                try:
                    self._mpv.command("sub-add", self._ass_path, "select")
                except Exception:
                    pass

    def play(self) -> None:
        if self._mpv is not None:
            self._mpv.pause = False

    def pause(self) -> None:
        if self._mpv is not None:
            self._mpv.pause = True

    def toggle_pause(self) -> None:
        if self._mpv is not None:
            self._mpv.pause = not self._mpv.pause

    def seek(self, seconds: float, flags: str = "absolute+exact") -> None:
        """Seek to the given time in seconds."""
        if self._mpv is not None:
            self._mpv.command("seek", str(seconds), flags)

    def seek_absolute(self, seconds: float) -> None:
        """Seek to an absolute time position."""
        if self._mpv is not None:
            self._mpv.time_pos = seconds

    def frame_step(self, forward: bool = True) -> None:
        """Step one frame forward or backward."""
        if self._mpv is not None:
            self._mpv.command("frame-step" if forward else "frame-back-step")

    @property
    def time_pos(self) -> float:
        if self._mpv is not None:
            try:
                val = self._mpv.time_pos
                return val if val is not None else 0.0
            except Exception:
                return 0.0
        return 0.0

    @property
    def duration(self) -> float:
        if self._mpv is not None:
            try:
                val = self._mpv.duration
                return val if val is not None else 0.0
            except Exception:
                return 0.0
        return 0.0

    @property
    def fps(self) -> float:
        if self._mpv is not None:
            try:
                val = self._mpv.container_fps
                return val if val is not None else 24.0
            except Exception:
                return 24.0
        return 24.0

    @property
    def video_dimensions(self) -> tuple[int, int] | None:
        if self._mpv is not None:
            try:
                w = self._mpv.video_params.get("w")
                h = self._mpv.video_params.get("h")
                if w and h:
                    return (int(w), int(h))
            except Exception:
                pass
        return None

    @property
    def is_file_loaded(self) -> bool:
        return self._file_loaded_flag

    @property
    def is_paused(self) -> bool:
        if self._mpv is not None:
            try:
                return bool(self._mpv.pause)
            except Exception:
                return True
        return True

    def capture_frame(self) -> tuple[bytes, int, int] | None:
        """Capture the current frame as raw RGB data.

        Returns (rgb_bytes, width, height) or None on failure.
        Uses mpv's screenshot-raw command to get the video frame.
        """
        if self._mpv is None:
            return None
        try:
            img = self._mpv.screenshot_raw(includes="video")
            if img is None:
                return None
            # img is a PIL Image in RGB mode
            w, h = img.size
            return img.tobytes(), w, h
        except Exception:
            return None

    def shutdown(self) -> None:
        """Clean up mpv resources. Call before the widget is destroyed."""
        self._shutting_down = True
        if self._ctx is not None:
            self._ctx.free()
            self._ctx = None
        if self._mpv is not None:
            self._mpv.terminate()
            self._mpv = None
