"""RetimeBar — horizontal bar with retime controls for the current selection.

Visible only when ``LabelStore.selected`` is non-empty. Contains:
  - Set In / Set Out buttons (mark at current playhead time)
  - Start / End numeric fields (accept absolute + relative input)
  - −1f / +1f frame nudge buttons (shift selection preserving duration)
  - Shift… popover for arbitrary shifts

Thin: emits user intent into ``RetimeController`` and refreshes from
``LabelStore`` signals. No business logic.

Spec: docs/superpowers/specs/2026-05-18-retiming-labels-design.md
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QLabel, QLineEdit, QPushButton, QInputDialog,
)

from sub_label_pos.model.ass_file import _seconds_to_time
from sub_label_pos.ui import theme

if TYPE_CHECKING:
    from sub_label_pos.model.label_store import LabelStore
    from sub_label_pos.ui.controllers.retime_controller import RetimeController


class RetimeBar(QWidget):
    """Horizontal retime bar. Construct, wire signals, then hand off to MainWindow."""

    def __init__(
        self,
        store: "LabelStore",
        controller: "RetimeController",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._controller = controller

        self.setStyleSheet(f"background: {theme.Tokens.bg_deepest};")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(6)

        # Mark In / Out
        self._set_in_btn = QPushButton("Set In  I")
        self._set_in_btn.clicked.connect(self._controller.set_in_at_current)
        layout.addWidget(self._set_in_btn)

        self._set_out_btn = QPushButton("Set Out  O")
        self._set_out_btn.clicked.connect(self._controller.set_out_at_current)
        layout.addWidget(self._set_out_btn)

        # Start field
        layout.addWidget(QLabel("Start"))
        self._start_field = QLineEdit()
        self._start_field.setPlaceholderText("H:MM:SS.cc")
        self._start_field.setFixedWidth(110)
        self._start_field.returnPressed.connect(self._commit_start)
        layout.addWidget(self._start_field)

        # End field
        layout.addWidget(QLabel("End"))
        self._end_field = QLineEdit()
        self._end_field.setPlaceholderText("H:MM:SS.cc")
        self._end_field.setFixedWidth(110)
        self._end_field.returnPressed.connect(self._commit_end)
        layout.addWidget(self._end_field)

        # Frame nudge
        self._minus_f_btn = QPushButton("−1f")
        self._minus_f_btn.clicked.connect(lambda: self._controller.nudge_both(-1))
        self._minus_f_btn.setToolTip("Shift selection by −1 frame (requires video for frame snapping)")
        layout.addWidget(self._minus_f_btn)

        self._plus_f_btn = QPushButton("+1f")
        self._plus_f_btn.clicked.connect(lambda: self._controller.nudge_both(+1))
        self._plus_f_btn.setToolTip("Shift selection by +1 frame (requires video for frame snapping)")
        layout.addWidget(self._plus_f_btn)

        # Shift popover
        self._shift_btn = QPushButton("Shift…")
        self._shift_btn.clicked.connect(self._open_shift_popover)
        layout.addWidget(self._shift_btn)

        # ×N badge
        self._badge = QLabel("")
        self._badge.setStyleSheet(
            f"color: {theme.Tokens.text_muted}; padding: 0 6px;"
        )
        layout.addWidget(self._badge)

        layout.addStretch()

        # Style fields with monospace + theme tokens.
        for field in (self._start_field, self._end_field):
            field.setStyleSheet(
                f"background: {theme.Tokens.bg_deepest};"
                f"color: {theme.Tokens.text_primary};"
                f"border: 1px solid {theme.Tokens.border};"
                f"border-radius: 4px; padding: 2px 4px;"
                f"font-family: ui-monospace, Menlo, Consolas, monospace;"
                f"font-size: 11px; font-variant-numeric: tabular-nums;"
            )

        # Capture the post-styling base for the flash-invalid effect.
        self._base_styles: dict[int, str] = {
            id(self._start_field): self._start_field.styleSheet(),
            id(self._end_field): self._end_field.styleSheet(),
            id(self._shift_btn): self._shift_btn.styleSheet(),
        }
        self._flash_timers: dict[int, QTimer] = {}

        # Subscribe to store signals
        store.selection_changed.connect(self._on_selection_changed)
        store.labels_mutated.connect(self._on_labels_mutated)

        # Initial state: hidden until selection appears
        self._on_selection_changed(store.selected)
        self._update_fps_dependent_state()

    # --- Signal handlers ---

    def _on_selection_changed(self, selected: set) -> None:
        if not selected:
            self.setVisible(False)
            return
        self.setVisible(True)
        self._refresh_fields()
        self._refresh_badge(len(selected))
        self._update_fps_dependent_state()

    def _on_labels_mutated(self, changed: set) -> None:
        if any(lid in changed for lid in self._store.selected):
            self._refresh_fields()

    def _update_fps_dependent_state(self) -> None:
        try:
            fps = self._controller.fps()
        except AttributeError:
            fps = 0.0
        enabled = fps > 0
        self._minus_f_btn.setEnabled(enabled)
        self._plus_f_btn.setEnabled(enabled)

    # --- Field commit handlers ---

    def _commit_start(self) -> None:
        text = self._start_field.text()
        ok = self._controller.apply_input_to_start(text)
        if not ok:
            self._flash_invalid(self._start_field)
            self._refresh_fields()

    def _commit_end(self) -> None:
        text = self._end_field.text()
        ok = self._controller.apply_input_to_end(text)
        if not ok:
            self._flash_invalid(self._end_field)
            self._refresh_fields()

    def _open_shift_popover(self) -> None:
        text, ok = QInputDialog.getText(
            self, "Shift selection",
            "Shift by (e.g. +250ms, -6f, +1.5s):",
        )
        if not ok or not text.strip():
            return
        from sub_label_pos.geometry.time_input import parse_relative_delta
        # Use the real fps so '+6f' / '-6f' compute correctly.
        delta = parse_relative_delta(text, fps=self._controller.fps())
        if delta is None:
            self._flash_invalid(self._shift_btn)
            return
        self._controller.shift(delta)

    # --- Refresh helpers ---

    def _refresh_fields(self) -> None:
        ids = list(self._store.selected)
        if not ids:
            return
        labels = [self._store.state.labels[lid] for lid in ids if lid in self._store.state.labels]
        if not labels:
            return
        starts = {round(lb.start_time, 2) for lb in labels}
        ends = {round(lb.end_time, 2) for lb in labels}
        # If the field has focus, leave it alone (user is typing).
        if not self._start_field.hasFocus():
            self._start_field.setText(_seconds_to_time(labels[0].start_time) if len(starts) == 1 else "")
            self._start_field.setPlaceholderText("—" if len(starts) > 1 else "H:MM:SS.cc")
        if not self._end_field.hasFocus():
            self._end_field.setText(_seconds_to_time(labels[0].end_time) if len(ends) == 1 else "")
            self._end_field.setPlaceholderText("—" if len(ends) > 1 else "H:MM:SS.cc")

    def _refresh_badge(self, count: int) -> None:
        self._badge.setText(f"×{count}" if count >= 2 else "")

    def _flash_invalid(self, widget: QWidget) -> None:
        """Briefly outline the widget in red to indicate parse failure.

        Re-entrant safe: a pending flash for the same widget is cancelled, and
        the restored style is always the captured base — never a mid-flash
        snapshot.
        """
        wid = id(widget)
        base_style = self._base_styles.get(wid, "")
        # Cancel any pending restore for this widget.
        existing_timer = self._flash_timers.get(wid)
        if existing_timer is not None:
            existing_timer.stop()
        widget.setStyleSheet(base_style + f"border: 1px solid {theme.Tokens.danger};")
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: widget.setStyleSheet(base_style))
        timer.start(800)
        self._flash_timers[wid] = timer
