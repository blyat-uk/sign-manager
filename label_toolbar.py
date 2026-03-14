from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QPushButton, QLabel, QComboBox, QCheckBox,
    QColorDialog, QInputDialog, QMenu, QWidgetAction,
)

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

_BTN_STYLE = """
    QPushButton {
        background: #3a3a3a;
        color: #ddd;
        border: 1px solid #555;
        border-radius: 3px;
        padding: 2px 8px;
        font-size: 12px;
        min-width: 24px;
    }
    QPushButton:hover { background: #505050; }
    QPushButton:pressed { background: #606060; }
"""

_ALIGN_BTN_STYLE = """
    QPushButton {
        background: #3a3a3a;
        color: #ddd;
        border: 1px solid #555;
        border-radius: 3px;
        padding: 2px 6px;
        font-size: 12px;
        min-width: 20px;
    }
    QPushButton:hover { background: #505050; }
    QPushButton:pressed { background: #606060; }
    QPushButton:checked {
        background: #1f5fa6;
        border-color: #4a9eff;
        color: #fff;
    }
"""

_SIZE_LABEL_STYLE = """
    QLabel {
        color: #eee;
        font-size: 12px;
        font-weight: bold;
        padding: 0 4px;
        min-width: 28px;
    }
"""

_COMBO_STYLE = """
    QComboBox {
        background: #3a3a3a;
        color: #ddd;
        border: 1px solid #555;
        border-radius: 3px;
        padding: 2px 4px;
        font-size: 11px;
        min-width: 60px;
        max-width: 120px;
    }
    QComboBox:hover { background: #505050; }
    QComboBox::drop-down { border: none; width: 16px; }
    QComboBox QAbstractItemView {
        background: #3a3a3a;
        color: #ddd;
        selection-background-color: #1f5fa6;
        border: 1px solid #555;
    }
"""


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

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            "LabelToolbar { background: #2d2d2d; border: 1px solid #555; border-radius: 4px; }"
        )
        self.setVisible(False)

        self._font_size = 36
        self._outline_width = 2.0
        self._primary_colour = "&H00FFFFFF&"
        self._outline_colour = "&H00000000&"
        self._current_style_name = "Label"


        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 3, 4, 3)
        layout.setSpacing(3)

        self._dup_btn = self._make_btn("Dup", self.duplicate_clicked.emit)
        self._del_btn = self._make_btn("Del", self.delete_clicked.emit)
        layout.addWidget(self._dup_btn)
        layout.addWidget(self._del_btn)

        # Style dropdown
        self._sep_style = self._make_sep()
        layout.addWidget(self._sep_style)
        self._style_combo = QComboBox()
        self._style_combo.setStyleSheet(_COMBO_STYLE)
        self._style_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._style_combo.currentTextChanged.connect(self._on_style_combo_changed)
        layout.addWidget(self._style_combo)

        self._sep1 = self._make_sep()
        layout.addWidget(self._sep1)

        self._minus_btn = self._make_btn("\u2212", self._decrease_size)
        self._size_label = QLabel(str(self._font_size))
        self._size_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._size_label.setStyleSheet(_SIZE_LABEL_STYLE)
        self._plus_btn = self._make_btn("+", self._increase_size)
        layout.addWidget(self._minus_btn)
        layout.addWidget(self._size_label)
        layout.addWidget(self._plus_btn)

        self._sep2 = self._make_sep()
        layout.addWidget(self._sep2)

        self._align_btns: dict[int, QPushButton] = {}
        for label_text, an_val in (("L", 4), ("C", 5), ("R", 6)):
            btn = QPushButton(label_text)
            btn.setStyleSheet(_ALIGN_BTN_STYLE)
            btn.setCheckable(True)
            btn.setAutoExclusive(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.clicked.connect(lambda _checked, a=an_val: self.alignment_changed.emit(a))
            layout.addWidget(btn)
            self._align_btns[an_val] = btn

        self._sep3 = self._make_sep()
        layout.addWidget(self._sep3)

        # Bold / Italic toggle buttons
        self._bold_btn = QPushButton("B")
        self._bold_btn.setStyleSheet(_ALIGN_BTN_STYLE)
        self._bold_btn.setCheckable(True)
        self._bold_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._bold_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        font = self._bold_btn.font()
        font.setBold(True)
        self._bold_btn.setFont(font)
        self._bold_btn.clicked.connect(lambda checked: self.bold_toggled.emit(checked))
        layout.addWidget(self._bold_btn)

        self._italic_btn = QPushButton("I")
        self._italic_btn.setStyleSheet(_ALIGN_BTN_STYLE)
        self._italic_btn.setCheckable(True)
        self._italic_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._italic_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        font = self._italic_btn.font()
        font.setItalic(True)
        self._italic_btn.setFont(font)
        self._italic_btn.clicked.connect(lambda checked: self.italic_toggled.emit(checked))
        layout.addWidget(self._italic_btn)

        self._sep4 = self._make_sep()
        layout.addWidget(self._sep4)

        # Primary colour swatch
        self._primary_colour_btn = QPushButton("Fc")
        self._primary_colour_btn.setFixedSize(24, 22)
        self._primary_colour_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._primary_colour_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._primary_colour_btn.clicked.connect(self._pick_primary_colour)
        layout.addWidget(self._primary_colour_btn)

        # Outline colour swatch
        self._outline_colour_btn = QPushButton("Oc")
        self._outline_colour_btn.setFixedSize(24, 22)
        self._outline_colour_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._outline_colour_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._outline_colour_btn.clicked.connect(self._pick_outline_colour)
        layout.addWidget(self._outline_colour_btn)

        self._sep5 = self._make_sep()
        layout.addWidget(self._sep5)

        # Outline width controls
        self._bord_minus_btn = self._make_btn("\u2212", self._decrease_outline)
        self._bord_label = QLabel(str(int(self._outline_width)))
        self._bord_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._bord_label.setStyleSheet(_SIZE_LABEL_STYLE)
        self._bord_plus_btn = self._make_btn("+", self._increase_outline)
        layout.addWidget(self._bord_minus_btn)
        layout.addWidget(self._bord_label)
        layout.addWidget(self._bord_plus_btn)

        self._sep6 = self._make_sep()
        layout.addWidget(self._sep6)

        self._copy_btn = self._make_btn("Copy", self._show_copy_menu)
        self._paste_btn = self._make_btn("Paste", self.paste_style_clicked.emit)
        layout.addWidget(self._copy_btn)
        layout.addWidget(self._paste_btn)

        self._sep7 = self._make_sep()
        layout.addWidget(self._sep7)

        self._apply_btn = self._make_btn("Apply", self.apply_style_clicked.emit)
        layout.addWidget(self._apply_btn)

        self.adjustSize()

    def _make_btn(self, text: str, slot) -> QPushButton:
        btn = QPushButton(text)
        btn.setStyleSheet(_BTN_STYLE)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.clicked.connect(slot)
        return btn

    def _make_sep(self) -> QLabel:
        sep = QLabel("|")
        sep.setStyleSheet("color: #666; padding: 0 2px;")
        return sep

    def _increase_size(self):
        self._font_size += _FONT_STEP
        self._size_label.setText(str(self._font_size))
        self.font_size_changed.emit(self._font_size)

    def _decrease_size(self):
        new_size = max(_MIN_FONT_SIZE, self._font_size - _FONT_STEP)
        if new_size != self._font_size:
            self._font_size = new_size
            self._size_label.setText(str(self._font_size))
            self.font_size_changed.emit(self._font_size)

    def _pick_primary_colour(self):
        initial = ass_colour_to_qcolor(self._primary_colour)
        colour = QColorDialog.getColor(initial, self, "Primary Colour")
        if colour.isValid():
            self._primary_colour = qcolor_to_ass_colour(colour)
            self._update_colour_btn(self._primary_colour_btn, colour)
            self.primary_colour_changed.emit(self._primary_colour)

    def _pick_outline_colour(self):
        initial = ass_colour_to_qcolor(self._outline_colour)
        colour = QColorDialog.getColor(initial, self, "Outline Colour")
        if colour.isValid():
            self._outline_colour = qcolor_to_ass_colour(colour)
            self._update_colour_btn(self._outline_colour_btn, colour)
            self.outline_colour_changed.emit(self._outline_colour)

    def _increase_outline(self):
        new_width = min(_MAX_OUTLINE, self._outline_width + _OUTLINE_STEP)
        if new_width != self._outline_width:
            self._outline_width = new_width
            self._bord_label.setText(str(int(self._outline_width)))
            self.outline_width_changed.emit(self._outline_width)

    def _decrease_outline(self):
        new_width = max(_MIN_OUTLINE, self._outline_width - _OUTLINE_STEP)
        if new_width != self._outline_width:
            self._outline_width = new_width
            self._bord_label.setText(str(int(self._outline_width)))
            self.outline_width_changed.emit(self._outline_width)

    def _update_colour_btn(self, btn: QPushButton, colour: QColor):
        luma = 0.299 * colour.red() + 0.587 * colour.green() + 0.114 * colour.blue()
        text_colour = "#000" if luma > 128 else "#fff"
        btn.setStyleSheet(
            f"QPushButton {{ background: {colour.name()}; color: {text_colour}; "
            f"border: 1px solid #555; border-radius: 3px; font-size: 10px; }}"
            f"QPushButton:hover {{ border-color: #aaa; }}"
        )

    def _on_style_combo_changed(self, text: str):
        if text == "Add new...":
            name, ok = QInputDialog.getText(self, "New Style", "Style name:")
            if ok and name.strip():
                self.create_style_requested.emit(name.strip())
            # Reset combo to the previous style
            self._style_combo.blockSignals(True)
            idx = self._style_combo.findText(self._current_style_name)
            if idx >= 0:
                self._style_combo.setCurrentIndex(idx)
            self._style_combo.blockSignals(False)
            return
        if text:
            self._current_style_name = text
            self.style_changed.emit(text)

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

    def set_alignment(self, alignment: int | None):
        effective = alignment if alignment in (4, 5, 6) else None
        for an, btn in self._align_btns.items():
            btn.blockSignals(True)
            btn.setChecked(an == effective)
            btn.blockSignals(False)

    def show_for_label(
        self,
        font_size: int,
        alignment: int | None = None,
        bold: bool = False,
        italic: bool = False,
        style_name: str = "Label",
        available_styles: list[str] | None = None,
        primary_colour: str = "&H00FFFFFF&",
        outline_colour: str = "&H00000000&",
        outline_width: float = 2.0,
    ):
        self._font_size = font_size
        self._size_label.setText(str(font_size))
        self.set_alignment(alignment)

        # Bold/Italic state
        self._bold_btn.blockSignals(True)
        self._bold_btn.setChecked(bold)
        self._bold_btn.blockSignals(False)

        self._italic_btn.blockSignals(True)
        self._italic_btn.setChecked(italic)
        self._italic_btn.blockSignals(False)

        # Colour state
        self._primary_colour = primary_colour
        self._outline_colour = outline_colour
        self._update_colour_btn(self._primary_colour_btn, ass_colour_to_qcolor(primary_colour))
        self._update_colour_btn(self._outline_colour_btn, ass_colour_to_qcolor(outline_colour))

        # Outline width
        self._outline_width = outline_width
        self._bord_label.setText(str(int(outline_width)))

        # Style dropdown — always show with "Add new..." option
        self._current_style_name = style_name
        self._style_combo.blockSignals(True)
        self._style_combo.clear()
        if available_styles:
            self._style_combo.addItems(available_styles)
        self._style_combo.addItem("Add new...")
        idx = self._style_combo.findText(style_name)
        if idx >= 0:
            self._style_combo.setCurrentIndex(idx)
        self._style_combo.blockSignals(False)

        self.adjustSize()
        self.show()

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

    def set_multi_mode(self, multi: bool):
        """In multi-select mode, hide single-label-only actions."""
        self._dup_btn.setVisible(not multi)
        self._copy_btn.setVisible(not multi)
        self._apply_btn.setVisible(not multi)
        self.adjustSize()
