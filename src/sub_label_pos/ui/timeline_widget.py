"""TimelineWidget — video scrubber with playback controls and label group markers."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal, QRectF, QSize
from PyQt6.QtGui import QPainter, QColor, QPen
from PyQt6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QLabel,
)

from sub_label_pos.model.ass_file import _seconds_to_time
from sub_label_pos.model.groups import DerivedGroupModel
from sub_label_pos.ui import theme


class _TrackWidget(QWidget):
    """Custom painted slider track with label group markers."""

    seeked = pyqtSignal(float)  # emitted with seconds when user clicks/drags
    group_clicked = pyqtSignal(int)  # emitted with group index when a marker is clicked

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._duration: float = 0.0
        self._position: float = 0.0  # current position in seconds
        self._group_ranges: list[tuple[float, float]] = []  # (start, end) in seconds
        self._dragging = False
        self.setMinimumHeight(24)
        self.setMaximumHeight(24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_duration(self, duration: float) -> None:
        self._duration = max(0.0, duration)
        self.update()

    def set_position(self, seconds: float) -> None:
        self._position = max(0.0, seconds)
        self.update()

    def set_group_ranges(self, ranges: list[tuple[float, float]]) -> None:
        self._group_ranges = ranges
        self.update()

    def _time_to_x(self, seconds: float) -> float:
        if self._duration <= 0:
            return 0.0
        margin = 4
        usable = self.width() - 2 * margin
        return margin + (seconds / self._duration) * usable

    def _x_to_time(self, x: float) -> float:
        margin = 4
        usable = self.width() - 2 * margin
        if usable <= 0:
            return 0.0
        t = ((x - margin) / usable) * self._duration
        return max(0.0, min(t, self._duration))

    def paintEvent(self, event) -> None:
        from PyQt6.QtGui import QPainter, QColor, QPen, QLinearGradient
        from PyQt6.QtCore import QRectF, Qt
        from sub_label_pos.ui import theme

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # Track background (centered horizontal rounded rect, ~8px tall)
        track_y = h // 2 - 4
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#222428"))
        p.drawRoundedRect(QRectF(0, track_y, w, 8), 4, 4)

        # Fill up to playhead (accent gradient)
        if self._duration > 0:
            fill_w = int(w * (self._position / self._duration))
            grad = QLinearGradient(0, 0, fill_w, 0)
            grad.setColorAt(0, QColor(theme.Tokens.accent_deep))
            grad.setColorAt(1, QColor(theme.Tokens.accent))
            p.setBrush(grad)
            p.drawRoundedRect(QRectF(0, track_y, fill_w, 8), 4, 4)

        # Group markers
        for start_s, end_s in self._group_ranges:
            if self._duration <= 0:
                continue
            start_x = (start_s / self._duration) * w
            end_x = (end_s / self._duration) * w
            marker_w = max(3, end_x - start_x)
            p.setBrush(QColor(180, 200, 220, 90))
            p.drawRoundedRect(QRectF(start_x, track_y, marker_w, 8), 2, 2)

        # Playhead — chunky white circle + glow
        if self._duration > 0:
            ph_x = (self._position / self._duration) * w
            # Glow line
            glow = QColor(theme.Tokens.text_emphasis)
            glow.setAlpha(100)
            p.setPen(QPen(glow, 4))
            p.drawLine(int(ph_x), 4, int(ph_x), h - 4)
            # Crisp line
            p.setPen(QPen(QColor(theme.Tokens.text_emphasis), 2))
            p.drawLine(int(ph_x), 4, int(ph_x), h - 4)
            # Circle grip
            p.setPen(QPen(QColor(theme.Tokens.bg_deepest), 2))
            p.setBrush(QColor(theme.Tokens.text_emphasis))
            p.drawEllipse(QRectF(ph_x - 6, h / 2 - 6, 12, 12))

        p.end()

    def _hit_group(self, x: float) -> int:
        """Return the group index if x is within a marker, or -1."""
        for i, (start, end) in enumerate(self._group_ranges):
            x1 = self._time_to_x(start)
            x2 = self._time_to_x(end)
            w = max(x2 - x1, 3)
            if x1 <= x <= x1 + w:
                return i
        return -1

    def mousePressEvent(self, event) -> None:
        if event and event.button() == Qt.MouseButton.LeftButton:
            x = event.position().x()
            gi = self._hit_group(x)
            if gi >= 0:
                self.group_clicked.emit(gi)
                return
            self._dragging = True
            t = self._x_to_time(x)
            self._position = t
            self.update()
            self.seeked.emit(t)

    def mouseMoveEvent(self, event) -> None:
        if event and self._dragging:
            t = self._x_to_time(event.position().x())
            self._position = t
            self.update()
            self.seeked.emit(t)

    def mouseReleaseEvent(self, event) -> None:
        if event and event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False


class TimelineWidget(QWidget):
    """Video timeline with scrubber, play/pause, and frame step controls.

    Signals:
        time_seeked(float): user dragged the scrubber to a new time
        play_toggled(bool): play button toggled (True = playing)
        step_requested(int): frame step button pressed (+1 or -1)
    """

    time_seeked = pyqtSignal(float)
    play_toggled = pyqtSignal(bool)
    step_requested = pyqtSignal(int)
    group_clicked = pyqtSignal(int)

    def __init__(self, groups: DerivedGroupModel, parent: QWidget | None = None):
        super().__init__(parent)
        self._groups = groups
        self._duration: float = 0.0
        self._playing = False

        self.setStyleSheet(f"background: {theme.Tokens.bg_deepest};")

        # Controls row
        controls = QHBoxLayout()
        controls.setContentsMargins(8, 4, 8, 0)
        controls.setSpacing(4)

        # Step backward
        self._back_btn = theme.IconButton(
            theme.Icons.step_back(),
            tooltip="Previous frame (←)",
            icon_only=True,
        )
        self._back_btn.clicked.connect(lambda: self.step_requested.emit(-1))
        controls.addWidget(self._back_btn)

        # Play/pause
        self._play_btn = theme.IconButton(
            theme.Icons.play(color=theme.Tokens.accent),
            tooltip="Play / Pause (Space)",
            icon_only=True,
        )
        self._play_btn.setFixedSize(34, 30)
        self._play_btn.setIconSize(QSize(22, 22))
        self._play_btn.clicked.connect(self._on_play_clicked)
        controls.addWidget(self._play_btn)

        # Step forward
        self._fwd_btn = theme.IconButton(
            theme.Icons.step_forward(),
            tooltip="Next frame (→)",
            icon_only=True,
        )
        self._fwd_btn.clicked.connect(lambda: self.step_requested.emit(1))
        controls.addWidget(self._fwd_btn)

        controls.addSpacing(8)

        # Time label
        self._time_label = QLabel("0:00:00.00 / 0:00:00.00")
        self._time_label.setStyleSheet(
            f"color: {theme.Tokens.text_primary}; font-family: ui-monospace, Menlo, Consolas, monospace; "
            f"font-size: 11px; font-variant-numeric: tabular-nums; padding: 0 8px; background: transparent;"
        )
        controls.addWidget(self._time_label)

        controls.addStretch()

        # Track (scrubber)
        self._track = _TrackWidget()
        self._track.seeked.connect(self._on_track_seeked)
        self._track.group_clicked.connect(self.group_clicked)

        # Subscribe to group model so markers stay in sync with state.
        groups.groups_changed.connect(self._on_groups_changed)
        self._refresh_markers()

        # Layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 4)
        layout.setSpacing(2)
        layout.addLayout(controls)
        layout.addWidget(self._track)

        self.setFixedHeight(54)

    # ── Slots (called externally to update state without emitting signals) ──

    def set_time(self, seconds: float) -> None:
        """Update displayed time without emitting time_seeked."""
        self._track.set_position(seconds)
        self._update_time_label(seconds)

    def set_duration(self, duration: float) -> None:
        self._duration = duration
        self._track.set_duration(duration)
        self._update_time_label(self._track._position)

    def _on_groups_changed(self, _changed_ids: set) -> None:
        """Repaint group markers from the model."""
        self._refresh_markers()

    def _refresh_markers(self) -> None:
        ranges = [(g.start, g.end) for g in self._groups.groups]
        self._track.set_group_ranges(ranges)

    def set_playing(self, playing: bool) -> None:
        """Update play/pause button state without emitting play_toggled."""
        self._playing = playing
        self._update_play_icon()

    # ── Internal ──

    def _update_time_label(self, current: float) -> None:
        cur = _seconds_to_time(current) if current >= 0 else "0:00:00.00"
        dur = _seconds_to_time(self._duration) if self._duration > 0 else "0:00:00.00"
        self._time_label.setText(f"{cur} / {dur}")

    def _on_play_clicked(self) -> None:
        self._playing = not self._playing
        self._update_play_icon()
        self.play_toggled.emit(self._playing)

    def _update_play_icon(self) -> None:
        icon = theme.Icons.pause(color=theme.Tokens.accent) if self._playing \
            else theme.Icons.play(color=theme.Tokens.accent)
        self._play_btn.setIcon(icon)

    def _on_track_seeked(self, seconds: float) -> None:
        self._update_time_label(seconds)
        self.time_seeked.emit(seconds)
