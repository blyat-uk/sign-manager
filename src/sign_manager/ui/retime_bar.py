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

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QWidget,
)

from sign_manager import shortcuts
from sign_manager.model.ass_file import _seconds_to_time
from sign_manager.ui import theme

if TYPE_CHECKING:
    from sign_manager.model.label_store import LabelStore
    from sign_manager.ui.controllers.retime_controller import RetimeController


_FIELD_QSS = (
    f"QLineEdit {{ background: {theme.Tokens.bg_deepest};"
    f" color: {theme.Tokens.text_primary};"
    f" border: 1px solid {theme.Tokens.border};"
    f" border-radius: 4px; padding: 3px 6px;"
    f" font-family: ui-monospace, Menlo, Consolas, monospace;"
    f" font-size: 11px; font-variant-numeric: tabular-nums; }}"
    f"QLineEdit:focus {{ border-color: {theme.Tokens.accent}; }}"
)


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

        # Slightly raised surface so the bar reads as a distinct tool tray
        # separate from the video canvas above and the timeline below.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            f"RetimeBar {{ background: {theme.Tokens.bg_surface};"
            f" border-top: 1px solid {theme.Tokens.border};"
            f" border-bottom: 1px solid {theme.Tokens.border}; }}"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(6)

        # Section eyebrow label — establishes that this row is a tool group.
        eyebrow = QLabel("RETIME")
        eyebrow.setStyleSheet(
            f"color: {theme.Tokens.accent}; font-size: 10px;"
            f" font-weight: 700; letter-spacing: 1px; padding: 0 4px;"
            f" background: transparent;"
        )
        layout.addWidget(eyebrow)
        layout.addWidget(self._make_sep())

        # Mark In: square-bracket-left character (NLE convention).
        # IconButton would strip text when icon_only=True, so we set
        # geometry + larger font explicitly to render the bracket as a glyph.
        set_in_shortcut = shortcuts.SET_IN.toString()
        set_out_shortcut = shortcuts.SET_OUT.toString()
        self._set_in_btn = theme.IconButton(
            text="[",
            tooltip=f"Mark In — set selected label start to current frame "
                    f"({set_in_shortcut})",
        )
        self._set_in_btn.setFixedSize(30, 30)
        self._set_in_btn.setStyleSheet(
            self._set_in_btn.styleSheet()
            + " QPushButton { font-size: 20px; font-weight: 800; padding: 0; }"
        )
        self._set_in_btn.clicked.connect(self._controller.set_in_at_current)
        layout.addWidget(self._set_in_btn)

        # Go to the label's start frame — preview the exact first frame.
        self._goto_in_btn = theme.IconButton(
            theme.Icons.prev_group(),
            tooltip="Jump player to the selected label's first frame "
                    "(confirm where it appears)",
            icon_only=True,
        )
        self._goto_in_btn.clicked.connect(self._controller.seek_to_in)
        layout.addWidget(self._goto_in_btn)

        layout.addSpacing(2)

        # Go to the label's end frame — preview the exact last frame.
        self._goto_out_btn = theme.IconButton(
            theme.Icons.next_group(),
            tooltip="Jump player to the selected label's last frame "
                    "(confirm where it disappears)",
            icon_only=True,
        )
        self._goto_out_btn.clicked.connect(self._controller.seek_to_out)
        layout.addWidget(self._goto_out_btn)

        # Mark Out: square-bracket-right character (NLE convention).
        self._set_out_btn = theme.IconButton(
            text="]",
            tooltip=f"Mark Out — set selected label end to current frame "
                    f"({set_out_shortcut})",
        )
        self._set_out_btn.setFixedSize(30, 30)
        self._set_out_btn.setStyleSheet(
            self._set_out_btn.styleSheet()
            + " QPushButton { font-size: 20px; font-weight: 800; padding: 0; }"
        )
        self._set_out_btn.clicked.connect(self._controller.set_out_at_current)
        layout.addWidget(self._set_out_btn)

        layout.addWidget(self._make_sep())

        # Start field
        start_label = QLabel("Start")
        start_label.setStyleSheet(
            f"color: {theme.Tokens.text_muted}; font-size: 10px;"
            f" text-transform: uppercase; letter-spacing: 0.5px;"
            f" background: transparent; padding: 0 2px;"
        )
        layout.addWidget(start_label)
        self._start_field = QLineEdit()
        self._start_field.setPlaceholderText("H:MM:SS.cc")
        self._start_field.setFixedWidth(120)
        self._start_field.setToolTip(
            "Start time. Accepts H:MM:SS.cc absolute or relative +250ms / -6f / "
            "+1.5s. Press Enter to commit, Esc to revert."
        )
        self._start_field.returnPressed.connect(self._commit_start)
        layout.addWidget(self._start_field)

        # End field
        end_label = QLabel("End")
        end_label.setStyleSheet(
            f"color: {theme.Tokens.text_muted}; font-size: 10px;"
            f" text-transform: uppercase; letter-spacing: 0.5px;"
            f" background: transparent; padding: 0 2px;"
        )
        layout.addWidget(end_label)
        self._end_field = QLineEdit()
        self._end_field.setPlaceholderText("H:MM:SS.cc")
        self._end_field.setFixedWidth(120)
        self._end_field.setToolTip(
            "End time. Same input syntax as Start. Press Enter to commit, Esc to revert."
        )
        self._end_field.returnPressed.connect(self._commit_end)
        layout.addWidget(self._end_field)

        layout.addWidget(self._make_sep())

        # Frame nudge (always shifts whole selection by 1 frame; preserves duration)
        nudge_prev = shortcuts.NUDGE_BOTH_PREV.toString()
        nudge_next = shortcuts.NUDGE_BOTH_NEXT.toString()
        self._minus_f_btn = theme.IconButton(
            text="−1f",
            tooltip=f"Shift selection back by 1 frame ({nudge_prev}). "
                    f"Disabled when no video is loaded.",
        )
        self._minus_f_btn.clicked.connect(lambda: self._controller.nudge_both(-1))
        layout.addWidget(self._minus_f_btn)

        self._plus_f_btn = theme.IconButton(
            text="+1f",
            tooltip=f"Shift selection forward by 1 frame ({nudge_next}). "
                    f"Disabled when no video is loaded.",
        )
        self._plus_f_btn.clicked.connect(lambda: self._controller.nudge_both(+1))
        layout.addWidget(self._plus_f_btn)

        # Shift… popover (arbitrary delta in ms / s / frames)
        self._shift_btn = theme.IconButton(
            theme.Icons.sync_times(),
            text="Shift…",
            tooltip="Shift entire selection by a custom amount (e.g. +250ms, -6f, +1.5s)",
        )
        self._shift_btn.clicked.connect(self._open_shift_popover)
        layout.addWidget(self._shift_btn)

        # ×N badge for multi-selection
        self._badge = QLabel("")
        self._badge.setStyleSheet(
            f"color: {theme.Tokens.text_muted}; font-size: 11px;"
            f" font-weight: 600; padding: 0 8px; background: transparent;"
        )
        self._badge.setToolTip("Number of selected labels being retimed together")
        self._badge.setVisible(False)
        layout.addWidget(self._badge)

        layout.addStretch()

        # Style fields with monospace + theme tokens.
        for field in (self._start_field, self._end_field):
            field.setStyleSheet(_FIELD_QSS)

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

    # --- Construction helpers ---

    def _make_sep(self) -> QWidget:
        """Vertical 1px divider matching the LabelToolbar separator style."""
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet(
            f"color: {theme.Tokens.border}; max-width: 1px; min-height: 20px;"
            f" background: transparent;"
        )
        return sep

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
        from sign_manager.geometry.time_input import parse_relative_delta
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
            self._start_field.setText(
                _seconds_to_time(labels[0].start_time) if len(starts) == 1 else "",
            )
            self._start_field.setPlaceholderText("—" if len(starts) > 1 else "H:MM:SS.cc")
        if not self._end_field.hasFocus():
            self._end_field.setText(
                _seconds_to_time(labels[0].end_time) if len(ends) == 1 else "",
            )
            self._end_field.setPlaceholderText("—" if len(ends) > 1 else "H:MM:SS.cc")

    def _refresh_badge(self, count: int) -> None:
        if count >= 2:
            self._badge.setText(f"×{count}")
            self._badge.setVisible(True)
        else:
            self._badge.setVisible(False)

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
        widget.setStyleSheet(base_style + f"QLineEdit, QPushButton {{ border: 1px solid {theme.Tokens.danger}; }}")
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: widget.setStyleSheet(base_style))
        timer.start(800)
        self._flash_timers[wid] = timer
