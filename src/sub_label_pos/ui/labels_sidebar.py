"""Right-side dock listing every label with start→end timestamps.

Click a row to seek + select the matching label. Live text search.
Subscribes to LabelStore signals for live updates.

Spec: docs/superpowers/specs/2026-05-18-labels-sidebar-design.md
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
)

from sub_label_pos.model.label_rows import LabelGroupRow
from sub_label_pos.ui import theme

if TYPE_CHECKING:
    from sub_label_pos.model.ass_file import LabelDialogue
    from sub_label_pos.model.label_store import LabelStore
    from sub_label_pos.model.types import LabelId


def _fmt_time(seconds: float) -> str:
    """Render a time as M:SS.mmm (or H:MM:SS.mmm for >= 1h)."""
    total_ms = int(seconds * 1000)
    h = total_ms // 3_600_000
    m = (total_ms // 60_000) % 60
    s = (total_ms // 1000) % 60
    ms = total_ms % 1000
    if h:
        return f"{h}:{m:02d}:{s:02d}.{ms:03d}"
    return f"{m}:{s:02d}.{ms:03d}"


class _RowWidget(QWidget):
    """A single row in the labels list: ts + badge on top, label texts below."""

    def __init__(self, group: LabelGroupRow, parent: QWidget | None = None):
        super().__init__(parent)
        # Mouse events pass through to the parent QListWidget so item clicks
        # and double-clicks register as list-row activations.
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self._group = group
        self._active = False
        self._dirty = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 7, 12, 7)
        outer.setSpacing(1)

        # First line: timestamp + optional ×N badge
        first = QHBoxLayout()
        first.setContentsMargins(0, 0, 0, 0)
        first.setSpacing(8)
        self._ts_label = QLabel(
            f"{_fmt_time(group.start_time)} → {_fmt_time(group.end_time)}"
        )
        first.addWidget(self._ts_label, 1)
        if len(group.labels) > 1:
            self._badge = QLabel(f"×{len(group.labels)}")
            first.addWidget(self._badge)
        else:
            self._badge = None
        outer.addLayout(first)

        # Body lines: one per label
        self._text_labels: list[QLabel] = []
        for i, lb in enumerate(group.labels):
            lbl = QLabel(lb.text)
            lbl.setWordWrap(False)
            lbl.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
            outer.addWidget(lbl)
            self._text_labels.append(lbl)

        self._apply_style()

    def group(self) -> LabelGroupRow:
        return self._group

    def set_active(self, active: bool) -> None:
        if self._active == active:
            return
        self._active = active
        self._apply_style()

    def set_dirty(self, dirty: bool) -> None:
        if self._dirty == dirty:
            return
        self._dirty = dirty
        self._apply_style()

    def _apply_style(self) -> None:
        # Container background
        bg = theme.Tokens.accent_deep if self._active else theme.Tokens.bg_deepest
        self.setStyleSheet(f"_RowWidget {{ background: {bg}; }}")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        ts_color = "rgba(255,255,255,.85)" if self._active else theme.Tokens.text_muted
        self._ts_label.setStyleSheet(
            f"color: {ts_color}; font-size: 10px; "
            f"font-variant-numeric: tabular-nums; background: transparent;"
        )

        if self._badge is not None:
            if self._active:
                self._badge.setStyleSheet(
                    f"background: rgba(255,255,255,0.22); color: #fff; "
                    f"font-size: 9.5px; padding: 1px 6px; border-radius: 7px; "
                    f"font-weight: 600;"
                )
            else:
                self._badge.setStyleSheet(
                    f"background: {theme.Tokens.bg_raised}; "
                    f"color: {theme.Tokens.text_muted}; "
                    f"font-size: 9.5px; padding: 1px 6px; border-radius: 7px; "
                    f"font-weight: 600;"
                )

        # Text colors: first label primary, subsequent muted
        for i, lbl in enumerate(self._text_labels):
            if i == 0:
                color = "#fff" if self._active else theme.Tokens.text_primary
                size = 11.5
            else:
                color = (
                    "rgba(255,255,255,.7)" if self._active else theme.Tokens.text_muted
                )
                size = 10.5
            lbl.setStyleSheet(
                f"color: {color}; font-size: {size}px; background: transparent;"
            )

        # Modified-dot painted in paintEvent below
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self._dirty:
            return
        from PyQt6.QtGui import QPainter, QColor
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.Tokens.alert))
        # 6×6 dot, 9px from top-right corner
        p.drawEllipse(self.width() - 15, 9, 6, 6)
        p.end()


class LabelsSidebar(QWidget):
    """Sidebar listing labels grouped by exact (start_time, end_time)."""

    # High-level intent signals consumed by MainWindow.
    row_clicked = pyqtSignal(object)              # LabelGroupRow
    row_edit_requested = pyqtSignal(object)       # LabelDialogue
    row_jump_requested = pyqtSignal(object)       # LabelGroupRow
    row_delete_requested = pyqtSignal(tuple)      # tuple[LabelDialogue, ...]

    def __init__(self, store: "LabelStore", parent: QWidget | None = None):
        super().__init__(parent)
        self._store = store
        self._rows: list[LabelGroupRow] = []
        self._dirty_ids: set["LabelId"] = set()
        self._rebuild_pending = True  # rebuild on first show

        self.setStyleSheet(f"LabelsSidebar {{ background: {theme.Tokens.bg_deepest}; }}")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Header
        self._header = QWidget()
        self._header.setStyleSheet(
            f"background: {theme.Tokens.bg_base}; "
            f"border-bottom: 1px solid {theme.Tokens.border};"
        )
        hl = QHBoxLayout(self._header)
        hl.setContentsMargins(12, 8, 12, 8)
        hl.setSpacing(6)
        self._title_label = QLabel("LABELS")
        self._title_label.setStyleSheet(
            f"color: {theme.Tokens.text_muted}; font-size: 10px; "
            f"text-transform: uppercase; letter-spacing: 0.6px; font-weight: 700;"
        )
        self._count_label = QLabel("· 0")
        self._count_label.setStyleSheet(
            f"color: {theme.Tokens.border_strong}; font-size: 10px; "
            f"font-weight: 500;"
        )
        hl.addWidget(self._title_label)
        hl.addWidget(self._count_label)
        hl.addStretch()
        outer.addWidget(self._header)

        # Search
        self._search_wrap = QWidget()
        self._search_wrap.setStyleSheet(
            f"background: {theme.Tokens.bg_base}; "
            f"border-bottom: 1px solid {theme.Tokens.border};"
        )
        sl = QHBoxLayout(self._search_wrap)
        sl.setContentsMargins(10, 6, 10, 6)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search…")
        self._search.setStyleSheet(
            f"QLineEdit {{ background: {theme.Tokens.bg_deepest}; "
            f"color: {theme.Tokens.text_primary}; "
            f"border: 1px solid {theme.Tokens.border}; "
            f"border-radius: 4px; padding: 4px 8px; font-size: 11px; }}"
            f"QLineEdit:focus {{ border-color: {theme.Tokens.accent}; }}"
        )
        sl.addWidget(self._search)
        outer.addWidget(self._search_wrap)

        # List
        self._list = QListWidget()
        self._list.setStyleSheet(
            f"QListWidget {{ background: {theme.Tokens.bg_deepest}; "
            f"border: none; outline: none; }}"
            f"QListWidget::item {{ padding: 0; border-bottom: 1px solid {theme.Tokens.bg_raised}; }}"
        )
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        outer.addWidget(self._list, 1)

    def set_dirty_ids(self, ids: set["LabelId"]) -> None:
        """Caller informs us which labels are unsaved. Triggers row repaint."""
        self._dirty_ids = set(ids)
        # Row widgets re-read dirty state on the next rebuild; for now just
        # mark pending. (Task 7 wires the per-row repaint.)
        self._rebuild_pending = True
