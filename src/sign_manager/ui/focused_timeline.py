"""FocusedTimeline — zoomed time strip for frame-precise retiming via drag.

Auto-fits to the current selection. Renders selected-label markers with
left/right drag handles. Provides Zoom −/+/Reset buttons in its header and
mouse-wheel zoom. Auto-pans during handle drag if the cursor approaches
the strip edge. Click on empty area seeks.

Visibility: hidden when selection is empty.

Spec: docs/superpowers/specs/2026-05-18-retiming-labels-design.md
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from PyQt6.QtCore import Qt, QTimer, QRectF, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from sign_manager.geometry.frame_time import snap_to_frame
from sign_manager.ui import theme

if TYPE_CHECKING:
    from sign_manager.model.label_store import LabelStore
    from sign_manager.ui.controllers.retime_controller import RetimeController


_MIN_VIEW_SEC = 4.0
_MAX_VIEW_SEC = 60.0
_FRAME_FLOOR_PX = 4.0   # zoom-in stops when 1 frame would occupy < 4 px


class _StripWidget(QWidget):
    """The actual track + ticks paintable area inside FocusedTimeline."""

    seeked = pyqtSignal(float)
    drag_started = pyqtSignal(str)    # "start" or "end"
    drag_updated = pyqtSignal(float)
    drag_ended = pyqtSignal()
    view_panned = pyqtSignal()        # emitted when auto-pan changes the view

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._view_start: float = 0.0
        self._view_end: float = 4.0
        self._playhead: float = 0.0
        self._fps: float = 24.0
        self._selected_ids: set = set()
        self._labels_by_id: dict = {}    # LabelId -> (start, end)
        self._drag_edge: Literal["start", "end"] | None = None
        self._auto_pan_timer = QTimer(self)
        self._auto_pan_timer.setInterval(16)
        self._auto_pan_timer.timeout.connect(self._auto_pan_tick)
        self._last_drag_x: float = 0.0
        self.setMinimumHeight(28)
        self.setMouseTracking(True)

    # --- Public API ---

    def set_view(self, start: float, end: float) -> None:
        self._view_start = max(0.0, start)
        self._view_end = max(self._view_start + 0.01, end)
        self.update()

    def set_playhead(self, t: float) -> None:
        self._playhead = max(0.0, t)
        self.update()

    def set_fps(self, fps: float) -> None:
        self._fps = fps
        self.update()

    def set_selection(self, ids: set, labels_by_id: dict) -> None:
        self._selected_ids = set(ids)
        self._labels_by_id = dict(labels_by_id)
        self.update()

    def view_span(self) -> float:
        return self._view_end - self._view_start

    # --- Coordinate math ---

    def _t_to_x(self, t: float) -> float:
        if self.view_span() <= 0:
            return 0.0
        return (t - self._view_start) / self.view_span() * self.width()

    def _x_to_t(self, x: float) -> float:
        if self.width() <= 0:
            return self._view_start
        return self._view_start + (x / self.width()) * self.view_span()

    def _pixels_per_frame(self) -> float:
        if self._fps <= 0:
            return float("inf")
        return self.width() / (self.view_span() * self._fps)

    # --- Painting ---

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # Background
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.Tokens.bg_deepest))
        p.drawRect(0, 0, w, h)

        # Track
        track_y = 4
        track_h = h - 16   # leave 12 px for ticks below
        p.setBrush(QColor(theme.Tokens.bg_surface))
        p.drawRoundedRect(QRectF(0, track_y, w, track_h), 4, 4)

        # Selected markers
        for lid in self._selected_ids:
            if lid not in self._labels_by_id:
                continue
            s, e = self._labels_by_id[lid]
            if e < self._view_start or s > self._view_end:
                continue
            x1 = max(0.0, self._t_to_x(s))
            x2 = min(float(w), self._t_to_x(e))
            mw = max(2.0, x2 - x1)
            fill = QColor(theme.Tokens.accent)
            fill.setAlpha(160)
            p.setBrush(fill)
            p.setPen(QPen(QColor(theme.Tokens.accent), 1))
            p.drawRoundedRect(QRectF(x1, track_y + 2, mw, track_h - 4), 3, 3)

            # Handles at both edges
            handle_w, handle_h = 6.0, track_h + 4
            handle_pen = QPen(QColor(theme.Tokens.accent), 1)
            p.setPen(handle_pen)
            p.setBrush(QColor(theme.Tokens.text_emphasis))
            if 0 <= x1 <= w:
                p.drawRoundedRect(QRectF(x1 - handle_w / 2, track_y - 2, handle_w, handle_h), 2, 2)
            if 0 <= x2 <= w:
                p.drawRoundedRect(QRectF(x2 - handle_w / 2, track_y - 2, handle_w, handle_h), 2, 2)

        # Playhead
        ph_x = self._t_to_x(self._playhead)
        if 0 <= ph_x <= w:
            p.setPen(QPen(QColor(theme.Tokens.text_emphasis), 2))
            p.drawLine(int(ph_x), 0, int(ph_x), track_y + track_h)

        # Ticks: major every 1s, minor every 0.1s (when view <= 10s) or every 1s (else)
        tick_y = track_y + track_h + 2
        tick_pen_major = QPen(QColor(theme.Tokens.text_muted), 1)
        tick_pen_minor = QPen(QColor(theme.Tokens.border), 1)
        font_color = QColor(theme.Tokens.text_muted)
        p.setFont(self.font())
        view_s = self.view_span()
        # Major ticks
        import math
        start_s = math.floor(self._view_start)
        end_s = math.ceil(self._view_end)
        for s in range(start_s, end_s + 1):
            x = self._t_to_x(float(s))
            if 0 <= x <= w:
                p.setPen(tick_pen_major)
                p.drawLine(int(x), tick_y, int(x), tick_y + 6)
                p.setPen(font_color)
                label = _short_time(float(s))
                p.drawText(int(x) + 2, tick_y + 10, label)
        # Minor ticks if view is small
        if view_s <= 10.0:
            t = math.floor(self._view_start * 10) / 10.0
            while t <= self._view_end:
                if abs(t - round(t)) > 1e-6:
                    x = self._t_to_x(t)
                    if 0 <= x <= w:
                        p.setPen(tick_pen_minor)
                        p.drawLine(int(x), tick_y, int(x), tick_y + 3)
                t += 0.1

        p.end()

    # --- Mouse interactions ---

    def mousePressEvent(self, event) -> None:
        if not event or event.button() != Qt.MouseButton.LeftButton:
            return
        x = event.position().x()
        edge = self._hit_handle(x)
        if edge is not None:
            self._drag_edge = edge
            self._last_drag_x = x
            self.drag_started.emit(edge)
            self._auto_pan_timer.start()
            return
        # Click on empty area = seek.
        t = snap_to_frame(self._x_to_t(x), self._fps)
        self.seeked.emit(t)

    def mouseMoveEvent(self, event) -> None:
        if not event:
            return
        if self._drag_edge is not None:
            x = event.position().x()
            self._last_drag_x = x
            t = snap_to_frame(self._x_to_t(x), self._fps)
            self.drag_updated.emit(t)
            # cursor stays as ew-resize during drag
            self.setCursor(Qt.CursorShape.SizeHorCursor)
            return
        # Hover: show ew-resize cursor over handles, default elsewhere.
        if self._hit_handle(event.position().x()) is not None:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, event) -> None:
        if event and event.button() == Qt.MouseButton.LeftButton and self._drag_edge is not None:
            self._drag_edge = None
            self._auto_pan_timer.stop()
            self.drag_ended.emit()
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def wheelEvent(self, event) -> None:
        """Mouse-wheel zoom centered on cursor x."""
        if not event:
            return
        delta = event.angleDelta().y()
        if delta == 0:
            return
        factor = 0.85 if delta > 0 else (1 / 0.85)
        cursor_x = event.position().x()
        anchor_t = self._x_to_t(cursor_x)
        new_span = max(_MIN_VIEW_SEC, min(_MAX_VIEW_SEC, self.view_span() * factor))
        # Frame-precision floor: don't zoom in past 4 px/frame
        if self._fps > 0 and (self.width() / (new_span * self._fps)) < _FRAME_FLOOR_PX:
            return
        # Anchor zoom on cursor: keep anchor_t at the same screen x.
        rel = (cursor_x / max(1.0, self.width()))
        new_start = anchor_t - new_span * rel
        self.set_view(max(0.0, new_start), max(0.0, new_start) + new_span)

    def _hit_handle(self, x: float) -> Literal["start", "end"] | None:
        for lid in self._selected_ids:
            if lid not in self._labels_by_id:
                continue
            s, e = self._labels_by_id[lid]
            x1 = self._t_to_x(s)
            x2 = self._t_to_x(e)
            if abs(x - x1) <= 4.0:
                return "start"
            if abs(x - x2) <= 4.0:
                return "end"
        return None

    def _auto_pan_tick(self) -> None:
        """While dragging, if cursor near edge, pan the view."""
        if self._drag_edge is None:
            return
        x = self._last_drag_x
        edge_zone = 30.0
        view_s = self.view_span()
        pan_speed = view_s * 0.04   # 4% of view per tick at the edge
        if x < edge_zone:
            self.set_view(max(0.0, self._view_start - pan_speed), self._view_end - pan_speed)
            self.view_panned.emit()
            t = snap_to_frame(self._x_to_t(x), self._fps)
            self.drag_updated.emit(t)
        elif x > self.width() - edge_zone:
            self.set_view(self._view_start + pan_speed, self._view_end + pan_speed)
            self.view_panned.emit()
            t = snap_to_frame(self._x_to_t(x), self._fps)
            self.drag_updated.emit(t)


def _short_time(seconds: float) -> str:
    m = int(seconds // 60)
    s = seconds - m * 60
    return f"{m}:{s:05.2f}"


class FocusedTimeline(QWidget):
    """The zoomed strip widget — header (label + zoom buttons) above the strip."""

    view_changed = pyqtSignal(float, float)  # emits (view_start, view_end) when visible window changes

    def __init__(
        self,
        store: "LabelStore",
        controller: "RetimeController",
        *, fps_provider,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._controller = controller
        self._fps_provider = fps_provider
        self._auto_fit = True

        # Match the RetimeBar's raised surface so the two read as one tool
        # tray. The thicker bottom border + extra bottom padding give clear
        # visual separation from the playback timeline below.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            f"FocusedTimeline {{ background: {theme.Tokens.bg_surface};"
            f" border-bottom: 2px solid {theme.Tokens.border_strong}; }}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 14)
        layout.setSpacing(2)

        header = QHBoxLayout()
        header.setSpacing(6)
        eyebrow = QLabel("FOCUSED")
        eyebrow.setStyleSheet(
            f"color: {theme.Tokens.accent}; font-size: 10px;"
            f" font-weight: 700; letter-spacing: 1px; padding: 0 4px;"
            f" background: transparent;"
        )
        header.addWidget(eyebrow)
        header.addWidget(self._make_sep())
        self._header_label = QLabel("")
        self._header_label.setStyleSheet(
            f"color: {theme.Tokens.text_muted}; font-size: 10px;"
            f" text-transform: uppercase; letter-spacing: 0.5px;"
            f" background: transparent; padding: 0 4px;"
        )
        header.addWidget(self._header_label)
        header.addStretch()

        self._zoom_out_btn = theme.IconButton(
            theme.Icons.minus(),
            tooltip="Zoom out the focused strip (mouse wheel up also zooms out)",
            icon_only=True,
        )
        self._zoom_in_btn = theme.IconButton(
            theme.Icons.plus(),
            tooltip="Zoom in the focused strip (mouse wheel down also zooms in). "
                    "Stops at 4 pixels per frame.",
            icon_only=True,
        )
        self._reset_btn = theme.IconButton(
            text="Reset",
            tooltip="Reset zoom to auto-fit the selection",
        )
        for btn in (self._zoom_out_btn, self._zoom_in_btn, self._reset_btn):
            header.addWidget(btn)
        layout.addLayout(header)

        self._strip = _StripWidget()
        layout.addWidget(self._strip)

        self._zoom_out_btn.clicked.connect(lambda: self._zoom_by(1.5))
        self._zoom_in_btn.clicked.connect(lambda: self._zoom_by(1 / 1.5))
        self._reset_btn.clicked.connect(self._reset_to_auto_fit)

        self._strip.drag_started.connect(self._on_drag_started)
        self._strip.drag_updated.connect(self._on_drag_updated)
        self._strip.drag_ended.connect(self._on_drag_ended)
        # When auto-pan changes the view, mirror to outer view_changed signal.
        self._strip.view_panned.connect(
            lambda: self.view_changed.emit(self._strip._view_start, self._strip._view_end),
        )

        store.selection_changed.connect(self._on_selection_changed)
        store.labels_mutated.connect(self._on_labels_mutated)

        # Initial state: hidden until selection exists
        self._on_selection_changed(store.selected)

    # --- Public hooks ---

    def set_playhead(self, t: float) -> None:
        self._strip.set_playhead(t)

    def recenter_to(self, midpoint: float) -> None:
        """Pan the focused strip so its center lands on the given time."""
        self._auto_fit = False
        span = self._strip.view_span()
        self._set_view(max(0.0, midpoint - span / 2.0),
                       max(0.0, midpoint - span / 2.0) + span)

    def view_window(self) -> tuple[float, float]:
        return (self._strip._view_start, self._strip._view_end)

    # --- Public signal proxies for MainWindow to wire ---

    @property
    def seeked(self):
        return self._strip.seeked

    # --- Slots ---

    def _on_selection_changed(self, selected: set) -> None:
        if not selected:
            self.setVisible(False)
            return
        self.setVisible(True)
        self._strip.set_fps(self._fps_provider())
        self._refresh_labels()
        if self._auto_fit:
            self._fit_to_selection()
        self._refresh_header()

    def _on_labels_mutated(self, changed: set) -> None:
        if any(lid in changed for lid in self._store.selected):
            self._refresh_labels()
            self._refresh_header()

    def _on_drag_started(self, edge: str) -> None:
        self._controller.begin_drag(edge)

    def _on_drag_updated(self, t: float) -> None:
        self._controller.update_drag(t)

    def _on_drag_ended(self) -> None:
        self._controller.end_drag()

    # --- Internal ---

    def _make_sep(self) -> QWidget:
        """Vertical 1px divider matching the RetimeBar separator style."""
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet(
            f"color: {theme.Tokens.border}; max-width: 1px; min-height: 16px;"
            f" background: transparent;"
        )
        return sep

    def _set_view(self, start: float, end: float) -> None:
        """Centralised set-view that also notifies the outer view_changed signal."""
        self._strip.set_view(start, end)
        self._refresh_header()
        self.view_changed.emit(start, end)

    def _refresh_labels(self) -> None:
        labels_by_id = {}
        for lid in self._store.state.labels:
            lb = self._store.state.labels[lid]
            labels_by_id[lid] = (lb.start_time, lb.end_time)
        self._strip.set_selection(self._store.selected, labels_by_id)

    def _refresh_header(self) -> None:
        s, e = self._strip._view_start, self._strip._view_end
        self._header_label.setText(f"Focused · {e - s:.1f}s window")

    def _fit_to_selection(self) -> None:
        sel = [self._store.state.labels[lid] for lid in self._store.selected
               if lid in self._store.state.labels]
        if not sel:
            return
        min_s = min(lb.start_time for lb in sel)
        max_e = max(lb.end_time for lb in sel)
        span = max_e - min_s
        view = max(_MIN_VIEW_SEC, min(_MAX_VIEW_SEC, max(span * 3.0, span + 4.0)))
        midpoint = (min_s + max_e) / 2.0
        self._set_view(max(0.0, midpoint - view / 2.0),
                       max(0.0, midpoint - view / 2.0) + view)

    def _zoom_by(self, factor: float) -> None:
        self._auto_fit = False
        center = (self._strip._view_start + self._strip._view_end) / 2.0
        new_span = max(_MIN_VIEW_SEC, min(_MAX_VIEW_SEC, self._strip.view_span() * factor))
        fps = self._fps_provider()
        if fps > 0 and (self._strip.width() / (new_span * fps)) < _FRAME_FLOOR_PX:
            return
        self._set_view(max(0.0, center - new_span / 2.0),
                       max(0.0, center - new_span / 2.0) + new_span)

    def _reset_to_auto_fit(self) -> None:
        self._auto_fit = True
        self._fit_to_selection()
