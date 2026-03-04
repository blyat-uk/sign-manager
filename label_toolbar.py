from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QPushButton, QLabel

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

_SIZE_LABEL_STYLE = """
    QLabel {
        color: #eee;
        font-size: 12px;
        font-weight: bold;
        padding: 0 4px;
        min-width: 28px;
    }
"""


class LabelToolbar(QWidget):
    """Floating toolbar shown above a selected label."""

    duplicate_clicked = pyqtSignal()
    delete_clicked = pyqtSignal()
    font_size_changed = pyqtSignal(int)
    copy_style_clicked = pyqtSignal()
    paste_style_clicked = pyqtSignal()

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

    def show_for_label(self, font_size: int):
        self._font_size = font_size
        self._size_label.setText(str(font_size))
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
