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

# Gallery toggle
TOGGLE_GALLERY = QKeySequence(Qt.Key.Key_G)

# Labels sidebar toggle
TOGGLE_LABELS_SIDEBAR = QKeySequence(Qt.Key.Key_L)

# Per-label clipboard
DUPLICATE = QKeySequence("Ctrl+D")
PASTE_STYLE = QKeySequence("Ctrl+Shift+V")

# Retiming
SET_IN = QKeySequence(Qt.Key.Key_I)              # bare I; Ctrl+I (Italic) still works in text inputs
SET_OUT = QKeySequence(Qt.Key.Key_O)             # bare O
NUDGE_BOTH_PREV = QKeySequence("Shift+Left")     # shift selection by -1 frame
NUDGE_BOTH_NEXT = QKeySequence("Shift+Right")    # shift selection by +1 frame
