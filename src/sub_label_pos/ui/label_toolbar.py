from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QColorDialog, QInputDialog,
    QMenu, QWidgetAction, QCheckBox, QPushButton, QFrame,
)

from sub_label_pos.ui import theme
from sub_label_pos import shortcuts

if TYPE_CHECKING:
    from sub_label_pos.model.ass_file import AssStyle, LabelDialogue
    from sub_label_pos.model.label_store import LabelStore
    from sub_label_pos.model.types import LabelId

_MIN_FONT_SIZE = 8
_FONT_STEP = 2
_MIN_OUTLINE = 0
_MAX_OUTLINE = 10
_OUTLINE_STEP = 1


def ass_colour_to_qcolor(ass_colour: str) -> QColor:
    """Convert &HBBGGRR& or &HAABBGGRR& to QColor."""
    s = ass_colour.strip().lstrip("&Hh").rstrip("&")
    if len(s) == 6:
        b, g, r = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
        return QColor(r, g, b)
    elif len(s) == 8:
        a, b, g, r = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16), int(s[6:8], 16)
        return QColor(r, g, b, 255 - a)
    return QColor(255, 255, 255)


def qcolor_to_ass_colour(qcolor: QColor) -> str:
    """Convert QColor to &H00BBGGRR& format."""
    return f"&H00{qcolor.blue():02X}{qcolor.green():02X}{qcolor.red():02X}&"


_MENU_STYLE = """
    QMenu {
        background: #3a3a3a;
        color: #ddd;
        border: 1px solid #555;
        padding: 4px 0;
        font-size: 12px;
    }
    QMenu::item {
        padding: 4px 12px;
    }
    QMenu::item:selected { background: #505050; }
    QMenu::indicator {
        width: 14px; height: 14px;
        margin-left: 6px;
    }
    QMenu::indicator:checked { image: none; background: #1f5fa6; border: 1px solid #4a9eff; border-radius: 2px; }
    QMenu::indicator:unchecked { image: none; background: #2a2a2a; border: 1px solid #555; border-radius: 2px; }
    QMenu::separator { height: 1px; background: #555; margin: 4px 8px; }
"""

_COPY_ATTR_LABELS = [
    ("font_size", "Font Size"),
    ("alignment", "Alignment"),
    ("bold", "Bold"),
    ("italic", "Italic"),
    ("style", "Style"),
    ("primary_colour", "Primary Colour"),
    ("outline_colour", "Outline Colour"),
    ("outline_width", "Outline Width"),
    ("rotation", "Rotation"),
    ("position", "Position"),
]


class LabelToolbar(QWidget):
    """Floating toolbar shown above a selected label."""

    duplicate_clicked = pyqtSignal()
    delete_clicked = pyqtSignal()
    font_size_changed = pyqtSignal(int)
    alignment_changed = pyqtSignal(int)
    copy_style_clicked = pyqtSignal(set)
    paste_style_clicked = pyqtSignal()
    bold_toggled = pyqtSignal(bool)
    italic_toggled = pyqtSignal(bool)
    style_changed = pyqtSignal(str)
    primary_colour_changed = pyqtSignal(str)
    outline_colour_changed = pyqtSignal(str)
    outline_width_changed = pyqtSignal(float)
    apply_style_clicked = pyqtSignal()
    create_style_requested = pyqtSignal(str)

    def __init__(self, store: "LabelStore", parent: QWidget | None = None):
        super().__init__(parent)
        self._store = store
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            f"LabelToolbar {{ background: {theme.Tokens.bg_raised}; "
            f"border: 1px solid {theme.Tokens.border_strong}; "
            f"border-radius: 8px; }}"
        )
        self.setVisible(False)

        # Display-state mirrors — toolbar derives these from store signals;
        # outside code MUST NOT mutate them directly.
        self._font_size = 36
        self._outline_width = 2.0
        self._primary_colour = "&H00FFFFFF&"
        self._outline_colour = "&H00000000&"
        self._current_style_name = "Label"

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(2)

        # Style chip
        self._style_chip = theme.StyleChip()
        self._style_chip.clicked.connect(self._show_style_menu)
        layout.addWidget(self._style_chip)
        layout.addWidget(self._make_sep())

        # Duplicate / Delete
        self._dup_btn = theme.IconButton(
            theme.Icons.duplicate(),
            tooltip=f"Duplicate ({shortcuts.DUPLICATE.toString()})",
            icon_only=True,
        )
        self._dup_btn.clicked.connect(self.duplicate_clicked.emit)
        self._del_btn = theme.IconButton(
            theme.Icons.delete(),
            tooltip=f"Delete ({shortcuts.DELETE_SELECTED.toString()})",
            icon_only=True,
        )
        self._del_btn.clicked.connect(self.delete_clicked.emit)
        layout.addWidget(self._dup_btn)
        layout.addWidget(self._del_btn)
        layout.addWidget(self._make_sep())

        # Font size stepper
        self._size_stepper = theme.Stepper(
            initial=self._font_size, step=_FONT_STEP, minimum=_MIN_FONT_SIZE,
            minus_tooltip="Decrease font size", plus_tooltip="Increase font size",
        )
        self._size_stepper.value_changed.connect(self._on_size_changed)
        layout.addWidget(self._size_stepper)
        layout.addWidget(self._make_sep())

        # Alignment
        self._align_seg = theme.SegmentedToggle([
            (4, theme.Icons.align_left(), "Align left"),
            (5, theme.Icons.align_center(), "Align center"),
            (6, theme.Icons.align_right(), "Align right"),
        ])
        self._align_seg.selected.connect(lambda a: self.alignment_changed.emit(a))
        layout.addWidget(self._align_seg)
        layout.addWidget(self._make_sep())

        # Bold / Italic
        self._bold_btn = theme.IconButton(
            theme.Icons.bold(),
            tooltip=f"Bold ({shortcuts.BOLD.toString()})",
            icon_only=True,
        )
        self._bold_btn.setCheckable(True)
        self._bold_btn.clicked.connect(lambda checked: self.bold_toggled.emit(checked))
        self._italic_btn = theme.IconButton(
            theme.Icons.italic(),
            tooltip=f"Italic ({shortcuts.ITALIC.toString()})",
            icon_only=True,
        )
        self._italic_btn.setCheckable(True)
        self._italic_btn.clicked.connect(lambda checked: self.italic_toggled.emit(checked))
        layout.addWidget(self._bold_btn)
        layout.addWidget(self._italic_btn)
        layout.addWidget(self._make_sep())

        # Colors
        self._primary_swatch = theme.ColorSwatch(tooltip="Fill color")
        self._primary_swatch.clicked.connect(self._pick_primary_colour)
        self._outline_swatch = theme.ColorSwatch(tooltip="Outline color")
        self._outline_swatch.clicked.connect(self._pick_outline_colour)
        layout.addWidget(self._primary_swatch)
        layout.addWidget(self._outline_swatch)
        layout.addWidget(self._make_sep())

        # Outline width
        self._outline_stepper = theme.Stepper(
            initial=int(self._outline_width),
            step=_OUTLINE_STEP, minimum=_MIN_OUTLINE, maximum=_MAX_OUTLINE,
            minus_tooltip="Thinner outline", plus_tooltip="Thicker outline",
        )
        self._outline_stepper.value_changed.connect(
            lambda v: self.outline_width_changed.emit(float(v))
        )
        layout.addWidget(self._outline_stepper)
        layout.addWidget(self._make_sep())

        # Copy / Paste style
        self._copy_btn = theme.IconButton(
            theme.Icons.copy_style(),
            tooltip="Copy style…",
            icon_only=True,
        )
        self._copy_btn.clicked.connect(self._show_copy_menu)
        self._paste_btn = theme.IconButton(
            theme.Icons.paste_style(),
            tooltip=f"Paste style ({shortcuts.PASTE_STYLE.toString()})",
            icon_only=True,
        )
        self._paste_btn.clicked.connect(self.paste_style_clicked.emit)
        layout.addWidget(self._copy_btn)
        layout.addWidget(self._paste_btn)
        layout.addWidget(self._make_sep())

        # Promote-to-style (formerly "Apply")
        self._apply_btn = theme.IconButton(
            theme.Icons.promote_to_style(),
            tooltip="Promote overrides to style…",
            icon_only=True,
        )
        self._apply_btn.clicked.connect(self.apply_style_clicked.emit)
        layout.addWidget(self._apply_btn)

        self.adjustSize()

        # Subscribe to store signals — the toolbar is the SOLE owner of its
        # display state and updates itself from the store.
        self._store.selection_changed.connect(self._on_selection_changed)
        self._store.labels_mutated.connect(self._on_labels_mutated)

    def _make_sep(self) -> QWidget:
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet(
            f"color: {theme.Tokens.border}; max-width: 1px; min-height: 18px;"
        )
        return sep

    def _on_size_changed(self, v: int) -> None:
        self._font_size = v
        self.font_size_changed.emit(v)

    def _show_style_menu(self) -> None:
        """QMenu-driven style picker. Replaces the old QComboBox."""
        menu = QMenu(self)
        menu.setStyleSheet(_MENU_STYLE)
        for name in self._store.state.styles.keys():
            menu.addAction(name, lambda n=name: self._on_style_picked(n))
        menu.addSeparator()
        menu.addAction("Add new…", self._on_create_new_style)
        menu.exec(self._style_chip.mapToGlobal(self._style_chip.rect().bottomLeft()))

    def _on_style_picked(self, name: str) -> None:
        self._current_style_name = name
        self._style_chip.set_style_name(name)
        self.style_changed.emit(name)

    def _on_create_new_style(self) -> None:
        name, ok = QInputDialog.getText(self, "New Style", "Style name:")
        if ok and name.strip():
            self.create_style_requested.emit(name.strip())

    # --- color pickers ---
    def _pick_primary_colour(self):
        initial = ass_colour_to_qcolor(self._primary_colour)
        colour = QColorDialog.getColor(initial, self, "Primary Colour")
        if colour.isValid():
            self._primary_colour = qcolor_to_ass_colour(colour)
            self._primary_swatch.set_color(colour)
            self.primary_colour_changed.emit(self._primary_colour)

    def _pick_outline_colour(self):
        initial = ass_colour_to_qcolor(self._outline_colour)
        colour = QColorDialog.getColor(initial, self, "Outline Colour")
        if colour.isValid():
            self._outline_colour = qcolor_to_ass_colour(colour)
            self._outline_swatch.set_color(colour)
            self.outline_colour_changed.emit(self._outline_colour)

    def _show_copy_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet(_MENU_STYLE)
        checkboxes: list[tuple[str, QCheckBox]] = []

        # Toggle all button
        toggle_btn = QPushButton("Uncheck All")
        toggle_btn.setStyleSheet(
            "QPushButton { color: #aaa; background: transparent; border: none; "
            "padding: 4px 12px; font-size: 11px; text-align: left; }"
            "QPushButton:hover { color: #fff; }"
        )
        toggle_wa = QWidgetAction(menu)
        toggle_wa.setDefaultWidget(toggle_btn)
        menu.addAction(toggle_wa)

        menu.addSeparator()

        cb_style = "QCheckBox { color: #ddd; padding: 4px 12px; font-size: 12px; }"
        for key, display in _COPY_ATTR_LABELS:
            cb = QCheckBox(display)
            cb.setChecked(True)
            cb.setStyleSheet(cb_style)
            wa = QWidgetAction(menu)
            wa.setDefaultWidget(cb)
            menu.addAction(wa)
            checkboxes.append((key, cb))

        def _toggle_all():
            all_checked = all(cb.isChecked() for _, cb in checkboxes)
            for _, cb in checkboxes:
                cb.setChecked(not all_checked)
            toggle_btn.setText("Check All" if all_checked else "Uncheck All")

        def _update_toggle_text():
            all_checked = all(cb.isChecked() for _, cb in checkboxes)
            toggle_btn.setText("Uncheck All" if all_checked else "Check All")

        toggle_btn.clicked.connect(_toggle_all)
        for _, cb in checkboxes:
            cb.toggled.connect(lambda _: _update_toggle_text())

        menu.addSeparator()
        copy_action = menu.addAction("Copy")
        copy_action.triggered.connect(lambda: self._do_copy(menu, checkboxes))

        # Position below the Copy button
        pos = self._copy_btn.mapToGlobal(self._copy_btn.rect().bottomLeft())
        menu.exec(pos)

    def _do_copy(self, menu: QMenu, checkboxes: list[tuple[str, QCheckBox]]):
        menu.close()
        selected = {k for k, cb in checkboxes if cb.isChecked()}
        self.copy_style_clicked.emit(selected)

    # --- Store-signal slots -------------------------------------------

    def _on_selection_changed(self, selected: set) -> None:
        """Update toolbar display to reflect the new selection.

        On empty selection the toolbar is hidden. On non-empty selection the
        toolbar reads each selected label from the store, derives the display
        state, and shows itself. Positioning is left to the parent (MainWindow
        / J4) since it requires widget-coordinate label rects.
        """
        if not selected:
            self.hide()
            return
        self._sync_from_selection(set(selected))
        self.adjustSize()
        self.show()

    def _on_labels_mutated(self, affected: set) -> None:
        """If any affected label is in our selection, refresh display."""
        sel = self._store.selected
        if not sel:
            return
        if sel & set(affected):
            self._sync_from_selection(sel)
            self.adjustSize()

    def _sync_from_selection(self, selected: set) -> None:
        """Read selected labels from the store and update visible state.

        For multi-select, fields whose values disagree between selected labels
        fall back to a neutral display (existing displayed value retained;
        toggle buttons cleared). For single-select, fields show the label's
        resolved value (per-label override OR the style default).
        """
        state = self._store.state
        labels: list[LabelDialogue] = [
            state.labels[lid] for lid in selected if lid in state.labels
        ]
        if not labels:
            # Selection refers to ids the store doesn't know about. Hide so
            # the user doesn't act on stale data.
            self.hide()
            return

        styles = state.styles
        multi = len(labels) > 1

        # --- Single/multi mode toggles ---
        self._dup_btn.setVisible(not multi)
        self._copy_btn.setVisible(not multi)
        self._apply_btn.setVisible(not multi)

        # --- Resolve per-attribute values (with style fallback) ---
        def resolved_font_size(lb: "LabelDialogue") -> int:
            if lb.font_size is not None:
                return lb.font_size
            st = styles.get(lb.style_name)
            return st.font_size if st else 36

        def resolved_bool(lb: "LabelDialogue", attr: str) -> bool:
            v = getattr(lb, attr)
            if v is not None:
                return v
            st = styles.get(lb.style_name)
            return getattr(st, attr) if st else False

        def resolved_str(lb: "LabelDialogue", attr: str, default: str) -> str:
            v = getattr(lb, attr)
            if v is not None:
                return v
            st = styles.get(lb.style_name)
            return getattr(st, attr) if st else default

        def resolved_float(lb: "LabelDialogue", attr: str, default: float) -> float:
            v = getattr(lb, attr)
            if v is not None:
                return v
            st = styles.get(lb.style_name)
            return getattr(st, attr) if st else default

        first = labels[0]
        font_size = resolved_font_size(first)
        if not multi or all(resolved_font_size(lb) == font_size for lb in labels):
            self._set_font_size_display(font_size)
        else:
            self._set_font_size_display(None)

        # Alignment is a per-label \an override; only the override matters here.
        alignments = {lb.alignment for lb in labels}
        eff_alignment = alignments.pop() if len(alignments) == 1 else None
        self._set_alignment_display(eff_alignment)

        bold = resolved_bool(first, "bold")
        if multi and not all(resolved_bool(lb, "bold") == bold for lb in labels):
            self._set_bold_display(None)
        else:
            self._set_bold_display(bold)

        italic = resolved_bool(first, "italic")
        if multi and not all(resolved_bool(lb, "italic") == italic for lb in labels):
            self._set_italic_display(None)
        else:
            self._set_italic_display(italic)

        primary = resolved_str(first, "primary_colour", "&H00FFFFFF&")
        if multi and not all(resolved_str(lb, "primary_colour", "&H00FFFFFF&") == primary for lb in labels):
            primary_display = None
        else:
            primary_display = primary
        self._set_primary_colour_display(primary_display)

        outline = resolved_str(first, "outline_colour", "&H00000000&")
        if multi and not all(resolved_str(lb, "outline_colour", "&H00000000&") == outline for lb in labels):
            outline_display = None
        else:
            outline_display = outline
        self._set_outline_colour_display(outline_display)

        owidth = resolved_float(first, "outline_width", 2.0)
        if multi and not all(resolved_float(lb, "outline_width", 2.0) == owidth for lb in labels):
            self._set_outline_width_display(None)
        else:
            self._set_outline_width_display(owidth)

        # Style chip: always populated; selected entry is the first label's
        # style (multi-select shows the first label's style by convention).
        self._set_styles_display(list(styles.keys()), first.style_name)

    # --- Display setters (None == "mixed / unknown") -------------------

    def _set_font_size_display(self, value: int | None) -> None:
        self._size_stepper.set_value(value)
        if value is not None:
            self._font_size = value

    def _set_alignment_display(self, alignment: int | None) -> None:
        self._align_seg.set_selected(alignment if alignment in (4, 5, 6) else None)

    def _set_bold_display(self, bold: bool | None) -> None:
        self._bold_btn.blockSignals(True)
        self._bold_btn.setChecked(bool(bold) if bold is not None else False)
        self._bold_btn.blockSignals(False)

    def _set_italic_display(self, italic: bool | None) -> None:
        self._italic_btn.blockSignals(True)
        self._italic_btn.setChecked(bool(italic) if italic is not None else False)
        self._italic_btn.blockSignals(False)

    def _set_primary_colour_display(self, colour: str | None) -> None:
        if colour is None:
            self._primary_swatch.set_color(None)
            return
        self._primary_colour = colour
        self._primary_swatch.set_color(ass_colour_to_qcolor(colour))

    def _set_outline_colour_display(self, colour: str | None) -> None:
        if colour is None:
            self._outline_swatch.set_color(None)
            return
        self._outline_colour = colour
        self._outline_swatch.set_color(ass_colour_to_qcolor(colour))

    def _set_outline_width_display(self, width: float | None) -> None:
        self._outline_stepper.set_value(int(width) if width is not None else None)
        if width is not None:
            self._outline_width = width

    def _set_styles_display(self, available_styles: list[str], current: str) -> None:
        self._current_style_name = current
        self._style_chip.set_style_name(current)

    # --- Geometry placement (called by parent after layout) -----------

    def position_above(self, rect_center_x: float, rect_top_y: float):
        """Center toolbar above the label rect, clamped to parent bounds."""
        w = self.width()
        h = self.height()
        x = int(rect_center_x - w / 2)
        y = int(rect_top_y - h - 6)

        parent = self.parentWidget()
        if parent:
            x = max(0, min(x, parent.width() - w))
            y = max(0, y)

        self.move(x, y)
