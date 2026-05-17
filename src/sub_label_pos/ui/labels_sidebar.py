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
