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

from sign_manager.model.label_rows import LabelGroupRow, group_labels_by_exact_timing
from sign_manager.model.text_transforms import TransformKind
from sign_manager.ui import theme

if TYPE_CHECKING:
    from sign_manager.model.ass_file import LabelDialogue
    from sign_manager.model.label_store import LabelStore
    from sign_manager.model.types import LabelId


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
    row_transform_requested = pyqtSignal(str, object)  # (label_id, TransformKind)

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

        self._search.textChanged.connect(self._on_search_changed)

        # List
        self._list = QListWidget()
        # Click → row_clicked. itemActivated covers Enter on focused row.
        self._list.itemClicked.connect(self._on_item_clicked)
        self._list.itemActivated.connect(self._on_item_clicked)
        # Right-click → context menu.
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_list_context_menu)
        self._list.setStyleSheet(
            f"QListWidget {{ background: {theme.Tokens.bg_deepest}; "
            f"border: none; outline: none; }}"
            f"QListWidget::item {{ padding: 0; border-bottom: 1px solid {theme.Tokens.bg_raised}; }}"
        )
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        outer.addWidget(self._list, 1)

        # Empty-state overlay (no labels / no matches / no file).
        self._empty_label = QLabel("", parent=self._list)
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setWordWrap(True)
        self._empty_label.setStyleSheet(
            f"color: {theme.Tokens.text_muted}; font-size: 11px; "
            f"padding: 30px 24px; background: transparent;"
        )
        self._empty_label.hide()

        # Subscribe to store signals.
        self._store.file_loaded.connect(self._on_file_loaded)
        self._store.labels_mutated.connect(self._on_labels_mutated)
        self._store.labels_added.connect(self._on_labels_structure_changed)
        self._store.labels_removed.connect(self._on_labels_structure_changed)
        self._store.selection_changed.connect(self._on_selection_changed)

    def set_dirty_ids(self, ids: set["LabelId"]) -> None:
        """Caller informs us which labels are unsaved. Triggers row repaint."""
        self._dirty_ids = set(ids)
        self._refresh_dirty_state()

    # ── Public API ──────────────────────────────────────────────────────

    def mark_clean(self) -> None:
        """Caller informs us all labels are now saved-state. Drops the
        accumulated dirty set and repaints all rows.
        """
        if not self._dirty_ids:
            return
        self._dirty_ids = set()
        self._refresh_dirty_state()

    # ── Internal ────────────────────────────────────────────────────────

    def _on_file_loaded(self, _path) -> None:
        self._dirty_ids = set()
        self._rebuild_pending = True
        if self.isVisible():
            self._rebuild()

    def _on_labels_mutated(self, ids: set) -> None:
        # Accumulate dirty ids and schedule a rebuild (grouping may have
        # changed if timing was edited).
        self._dirty_ids |= set(ids)
        self._rebuild_pending = True
        if self.isVisible():
            self._rebuild()

    def _on_labels_structure_changed(self, _ids: set) -> None:
        # Labels appeared or disappeared. Structural mutations emit
        # labels_added / labels_removed and never labels_mutated, so this is
        # the only signal telling us a deleted label must leave the list.
        self._rebuild_pending = True
        if self.isVisible():
            self._rebuild()

    def _on_selection_changed(self, selected: set) -> None:
        self._refresh_active_row(selected)

    def _on_search_changed(self, _text: str) -> None:
        """Hide rows that don't contain the search term in any label's text."""
        search_term = self._search.text().strip().lower()
        visible = 0
        for i in range(self._list.count()):
            item = self._list.item(i)
            row = self._rows[i]
            if not search_term:
                item.setHidden(False)
                visible += 1
            else:
                hit = any(search_term in lb.text.lower() for lb in row.labels)
                item.setHidden(not hit)
                if hit:
                    visible += 1
        total = sum(len(r.labels) for r in self._rows)
        if search_term:
            self._count_label.setText(f"· {visible} / {total}")
        else:
            self._count_label.setText(f"· {total}")
        self._update_empty_state()

    def _row_for_item(self, item) -> LabelGroupRow | None:
        if item is None:
            return None
        idx = self._list.row(item)
        if 0 <= idx < len(self._rows):
            return self._rows[idx]
        return None

    def _on_item_clicked(self, item) -> None:
        row = self._row_for_item(item)
        if row is not None:
            self.row_clicked.emit(row)

    def _on_list_context_menu(self, pos) -> None:
        from PyQt6.QtWidgets import QMenu
        item = self._list.itemAt(pos)
        row = self._row_for_item(item)
        if row is None:
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
            f"QMenu::separator {{ height: 1px; background: {theme.Tokens.border}; "
            f"margin: 4px 0; }}"
        )
        act_edit = menu.addAction(theme.Icons.edit_text(), "Edit text")
        act_edit.triggered.connect(
            lambda: self.row_edit_requested.emit(row.labels[0])
        )
        if len(row.labels) == 1:
            self._build_transform_submenu(menu, row.labels[0].label_id)
        act_jump = menu.addAction(theme.Icons.time(), "Jump to time")
        act_jump.triggered.connect(lambda: self.row_jump_requested.emit(row))
        menu.addSeparator()
        act_del = menu.addAction(
            theme.Icons.delete(color=theme.Tokens.danger),
            "Delete" if len(row.labels) == 1 else f"Delete all {len(row.labels)} labels",
        )
        act_del.triggered.connect(
            lambda: self.row_delete_requested.emit(row.labels)
        )
        menu.exec(self._list.mapToGlobal(pos))

    def _build_transform_submenu(self, menu, label_id: str) -> None:
        sub = menu.addMenu("Transform")
        items: list[tuple[str, TransformKind] | None] = [
            ("Spaces → line breaks", TransformKind.SPACES_TO_BREAKS),
            ("Line breaks → spaces", TransformKind.BREAKS_TO_SPACES),
            ("Balance 2 lines", TransformKind.BALANCE),
            ("Balance 3 lines", TransformKind.BALANCE_3),
            None,
            ("UPPERCASE", TransformKind.UPPERCASE),
            ("lowercase", TransformKind.LOWERCASE),
            ("Title Case", TransformKind.TITLE_CASE),
        ]
        for entry in items:
            if entry is None:
                sub.addSeparator()
                continue
            label, kind = entry
            action = sub.addAction(label)
            action.triggered.connect(
                lambda _checked=False, lid=label_id, k=kind: self.row_transform_requested.emit(lid, k)
            )

    def showEvent(self, event):
        super().showEvent(event)
        if self._rebuild_pending:
            self._rebuild()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Re-anchor the empty-state overlay over the list.
        self._empty_label.setGeometry(self._list.geometry())

    def _rebuild(self) -> None:
        """Recompute groups from the current store state and populate the list."""
        from PyQt6.QtCore import QSize
        from PyQt6.QtWidgets import QListWidgetItem

        self._rebuild_pending = False
        self._list.clear()

        labels = list(self._store.state.labels.values()) if self._store.state else []
        # Stable: sort by line_index first so identical-timing groups preserve
        # file order; group_labels_by_exact_timing keeps insertion order within
        # a group.
        labels.sort(key=lambda lb: lb.line_index)
        self._rows = group_labels_by_exact_timing(labels)
        self._count_label.setText(f"· {len(labels)}")

        search_term = self._search.text().strip().lower()
        for row in self._rows:
            widget = _RowWidget(row)
            widget.set_dirty(any(
                getattr(lb, "label_id", None) in self._dirty_ids for lb in row.labels
            ))
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, widget.sizeHint().height()))
            self._list.addItem(item)
            self._list.setItemWidget(item, widget)
            if search_term:
                hit = any(search_term in lb.text.lower() for lb in row.labels)
                item.setHidden(not hit)

        if search_term:
            visible = sum(1 for i in range(self._list.count()) if not self._list.item(i).isHidden())
            self._count_label.setText(f"· {visible} / {len(labels)}")

        # Re-apply active highlight after rebuild.
        self._refresh_active_row(self._store.selected)
        self._update_empty_state()

    def _refresh_active_row(self, selected: set) -> None:
        """Find the row whose group contains any selected label-id; mark active."""
        target_index = -1
        if selected:
            sel_ids = set(selected)
            for i, row in enumerate(self._rows):
                if any(
                    getattr(lb, "label_id", None) in sel_ids for lb in row.labels
                ):
                    target_index = i
                    break
        for i in range(self._list.count()):
            item = self._list.item(i)
            widget = self._list.itemWidget(item)
            if widget is not None:
                widget.set_active(i == target_index)
        if target_index >= 0:
            self._list.scrollToItem(
                self._list.item(target_index),
                self._list.ScrollHint.EnsureVisible,
            )

    def _refresh_dirty_state(self) -> None:
        """Re-apply the dirty flag to each visible row widget without rebuilding."""
        for i, row in enumerate(self._rows):
            widget = self._list.itemWidget(self._list.item(i))
            if widget is None:
                continue
            dirty = any(
                getattr(lb, "label_id", None) in self._dirty_ids for lb in row.labels
            )
            widget.set_dirty(dirty)

    def _update_empty_state(self) -> None:
        labels_total = sum(len(r.labels) for r in self._rows)
        search_term = self._search.text().strip()
        has_file = self._store.state is not None and bool(self._store.state.labels)

        if not has_file and not self._rows:
            self._empty_label.setText("Open a video to see its labels.")
            self._empty_label.show()
            return
        if labels_total == 0:
            self._empty_label.setText("No labels in this file.")
            self._empty_label.show()
            return
        if search_term:
            visible = sum(
                1 for i in range(self._list.count()) if not self._list.item(i).isHidden()
            )
            if visible == 0:
                self._empty_label.setText(f"No matches for \"{search_term}\".")
                self._empty_label.show()
                return
        self._empty_label.hide()
