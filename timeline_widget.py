"""TimelineWidget — video scrubber with playback controls and label group markers."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal, QRectF
from PyQt6.QtGui import QPainter, QColor, QPen
from PyQt6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QPushButton,
    QLabel,
)

from ass_parser import _seconds_to_time


class _TrackWidget(QWidget):
    """Custom painted slider track with label group markers."""

    seeked = pyqtSignal(float)  # emitted with seconds when user clicks/drags

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
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        h = self.height()
        track_y = h // 2 - 2
        track_h = 4

        # Track background
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(60, 60, 60))
        p.drawRoundedRect(QRectF(4, track_y, self.width() - 8, track_h), 2, 2)

        # Group markers
        marker_color = QColor(80, 130, 180, 140)
        p.setBrush(marker_color)
        for start, end in self._group_ranges:
            x1 = self._time_to_x(start)
            x2 = self._time_to_x(end)
            w = max(x2 - x1, 3)  # minimum 3px width so markers are visible
            p.drawRoundedRect(QRectF(x1, track_y - 2, w, track_h + 4), 2, 2)

        # Progress fill
        if self._duration > 0:
            pos_x = self._time_to_x(self._position)
            p.setBrush(QColor(100, 160, 220))
            p.drawRoundedRect(QRectF(4, track_y, pos_x - 4, track_h), 2, 2)

            # Playhead
            p.setBrush(QColor(220, 220, 220))
            p.setPen(QPen(QColor(40, 40, 40), 1))
            radius = 6
            p.drawEllipse(QRectF(pos_x - radius, h / 2 - radius, radius * 2, radius * 2))

        p.end()

    def mousePressEvent(self, event) -> None:
        if event and event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            t = self._x_to_time(event.position().x())
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

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._duration: float = 0.0
        self._playing = False

        self.setStyleSheet("background: #2d2d2d;")

        # Controls row
        controls = QHBoxLayout()
        controls.setContentsMargins(8, 4, 8, 0)
        controls.setSpacing(4)

        # Play/pause button
        self._play_btn = QPushButton("\u25B6")  # ▶
        self._play_btn.setFixedSize(28, 28)
        self._play_btn.setStyleSheet("""
            QPushButton {
                background: #404040; color: #ddd; border: 1px solid #555;
                border-radius: 4px; font-size: 12px;
            }
            QPushButton:hover { background: #505050; }
        """)
        self._play_btn.clicked.connect(self._on_play_clicked)
        controls.addWidget(self._play_btn)

        # Frame step backward
        self._back_btn = QPushButton("\u23EA")  # ⏪
        self._back_btn.setFixedSize(28, 28)
        self._back_btn.setStyleSheet("""
            QPushButton {
                background: #404040; color: #ddd; border: 1px solid #555;
                border-radius: 4px; font-size: 11px;
            }
            QPushButton:hover { background: #505050; }
        """)
        self._back_btn.clicked.connect(lambda: self.step_requested.emit(-1))
        controls.addWidget(self._back_btn)

        # Frame step forward
        self._fwd_btn = QPushButton("\u23E9")  # ⏩
        self._fwd_btn.setFixedSize(28, 28)
        self._fwd_btn.setStyleSheet("""
            QPushButton {
                background: #404040; color: #ddd; border: 1px solid #555;
                border-radius: 4px; font-size: 11px;
            }
            QPushButton:hover { background: #505050; }
        """)
        self._fwd_btn.clicked.connect(lambda: self.step_requested.emit(1))
        controls.addWidget(self._fwd_btn)

        controls.addSpacing(8)

        # Time label
        self._time_label = QLabel("0:00:00.00 / 0:00:00.00")
        self._time_label.setStyleSheet("color: #ccc; font-family: monospace; font-size: 12px;")
        controls.addWidget(self._time_label)

        controls.addStretch()

        # Track (scrubber)
        self._track = _TrackWidget()
        self._track.seeked.connect(self._on_track_seeked)

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

    def set_groups(self, groups: list) -> None:
        """Set label group markers on the track. Accepts list of LabelGroup."""
        ranges = []
        for g in groups:
            if g.labels:
                start = min(lb.start_time for lb in g.labels)
                end = max(lb.end_time for lb in g.labels)
                ranges.append((start, end))
        self._track.set_group_ranges(ranges)

    def set_playing(self, playing: bool) -> None:
        """Update play/pause button state without emitting play_toggled."""
        self._playing = playing
        self._play_btn.setText("\u23F8" if playing else "\u25B6")  # ⏸ or ▶

    # ── Internal ──

    def _update_time_label(self, current: float) -> None:
        cur = _seconds_to_time(current) if current >= 0 else "0:00:00.00"
        dur = _seconds_to_time(self._duration) if self._duration > 0 else "0:00:00.00"
        self._time_label.setText(f"{cur} / {dur}")

    def _on_play_clicked(self) -> None:
        self._playing = not self._playing
        self._play_btn.setText("\u23F8" if self._playing else "\u25B6")
        self.play_toggled.emit(self._playing)

    def _on_track_seeked(self, seconds: float) -> None:
        self._update_time_label(seconds)
        self.time_seeked.emit(seconds)
