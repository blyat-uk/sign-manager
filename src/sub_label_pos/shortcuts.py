"""Central registry of keyboard shortcut bindings.

All keyboard shortcuts used by the app are defined here. Each constant is a
QKeySequence object that callers can bind via QShortcut(...).
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence

# Undo / redo
UNDO = QKeySequence(QKeySequence.StandardKey.Undo)         # Ctrl+Z (platform-aware)
REDO = QKeySequence(QKeySequence.StandardKey.Redo)         # Ctrl+Shift+Z (mac) / Ctrl+Y (win)
REDO_ALT_Y = QKeySequence("Ctrl+Y")                         # explicit Ctrl+Y everywhere
REDO_ALT_SHIFT_Z = QKeySequence("Ctrl+Shift+Z")             # explicit Ctrl+Shift+Z everywhere

# Save
SAVE = QKeySequence(QKeySequence.StandardKey.Save)         # Ctrl+S

# Selection / deletion
DELETE_SELECTED = QKeySequence(Qt.Key.Key_Delete)

# Navigation (keep existing bindings the app already supports)
NEXT_FRAME = QKeySequence(Qt.Key.Key_Right)
PREV_FRAME = QKeySequence(Qt.Key.Key_Left)
NEXT_GROUP = QKeySequence("Ctrl+Right")
PREV_GROUP = QKeySequence("Ctrl+Left")
NEXT_FILE = QKeySequence("Ctrl+Shift+Right")
PREV_FILE = QKeySequence("Ctrl+Shift+Left")

# Text formatting (in inline editor)
BOLD = QKeySequence(QKeySequence.StandardKey.Bold)         # Ctrl+B
ITALIC = QKeySequence(QKeySequence.StandardKey.Italic)     # Ctrl+I
