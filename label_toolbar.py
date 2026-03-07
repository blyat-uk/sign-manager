from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QPushButton, QLabel, QComboBox

_MIN_FONT_SIZE = 8
_FONT_STEP = 2

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


class LabelToolbar(QWidget):
    """Floating toolbar shown above a selected label."""

    duplicate_clicked = pyqtSignal()
    delete_clicked = pyqtSignal()
    font_size_changed = pyqtSignal(int)
    alignment_changed = pyqtSignal(int)
    copy_style_clicked = pyqtSignal()
    paste_style_clicked = pyqtSignal()
    bold_toggled = pyqtSignal(bool)
    italic_toggled = pyqtSignal(bool)
    style_changed = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            "LabelToolbar { background: #2d2d2d; border: 1px solid #555; border-radius: 4px; }"
        )
        self.setVisible(False)

        self._font_size = 36

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

        self._copy_btn = self._make_btn("Copy", self.copy_style_clicked.emit)
        self._paste_btn = self._make_btn("Paste", self.paste_style_clicked.emit)
        layout.addWidget(self._copy_btn)
        layout.addWidget(self._paste_btn)

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

    def _on_style_combo_changed(self, text: str):
        if text:
            self.style_changed.emit(text)

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

        # Style dropdown
        if available_styles and len(available_styles) > 1:
            self._style_combo.blockSignals(True)
            self._style_combo.clear()
            self._style_combo.addItems(available_styles)
            idx = self._style_combo.findText(style_name)
            if idx >= 0:
                self._style_combo.setCurrentIndex(idx)
            self._style_combo.blockSignals(False)
            self._style_combo.show()
            self._sep_style.show()
        else:
            self._style_combo.hide()
            self._sep_style.hide()

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
        self.adjustSize()
