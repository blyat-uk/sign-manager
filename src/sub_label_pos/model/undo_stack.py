"""Bounded undo/redo stack with same-key coalescing."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

from PyQt6.QtCore import QObject, pyqtSignal

from sub_label_pos.model.mutations import Mutation


@dataclass
class _Entry:
    forward: Mutation
    inverse: Mutation
    timestamp: float


class UndoStack(QObject):
    """Bounded undo/redo history with same-key coalescing.

    Coalescing: when two mutations with the same non-None ``coalesce_key`` are
    pushed within ``coalesce_window_ms`` of each other, the second forward
    replaces the first while the ORIGINAL inverse is preserved. This means a
    user dragging a label produces many MoveLabel events but one undo step
    that restores the original position.

    Bounded: oldest undo entries are dropped when exceeding ``max_size``.

    Signals:
      can_undo_changed(bool): emitted when can_undo transitions
      can_redo_changed(bool): emitted when can_redo transitions
    """

    can_undo_changed = pyqtSignal(bool)
    can_redo_changed = pyqtSignal(bool)

    def __init__(self, *, max_size: int = 200, coalesce_window_ms: int = 500) -> None:
        super().__init__()
        self._max = max_size
        self._coalesce_ms = coalesce_window_ms
        self._undo: deque[_Entry] = deque()
        self._redo: deque[_Entry] = deque()

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def size(self) -> int:
        return len(self._undo)

    def push(self, forward: Mutation, inverse: Mutation) -> None:
        now = time.monotonic()
        key = forward.coalesce_key
        # Coalesce path: same key within window
        if (
            key is not None
            and self._undo
            and self._undo[-1].forward.coalesce_key == key
            and (now - self._undo[-1].timestamp) * 1000 <= self._coalesce_ms
        ):
            last = self._undo[-1]
            self._undo[-1] = _Entry(
                forward=forward, inverse=last.inverse, timestamp=now,
            )
            return
        # Non-coalesce path: clear redo, append, evict if needed
        was_undoable = bool(self._undo)
        had_redo = bool(self._redo)
        self._redo.clear()
        self._undo.append(_Entry(forward=forward, inverse=inverse, timestamp=now))
        while len(self._undo) > self._max:
            self._undo.popleft()
        if not was_undoable and self._undo:
            self.can_undo_changed.emit(True)
        if had_redo:
            self.can_redo_changed.emit(False)

    def undo(self) -> Mutation:
        if not self._undo:
            raise IndexError("undo stack is empty")
        entry = self._undo.pop()
        was_redoable = bool(self._redo)
        self._redo.append(entry)
        if not self._undo:
            self.can_undo_changed.emit(False)
        if not was_redoable:
            self.can_redo_changed.emit(True)
        return entry.inverse

    def redo(self) -> Mutation:
        if not self._redo:
            raise IndexError("redo stack is empty")
        entry = self._redo.pop()
        was_undoable = bool(self._undo)
        self._undo.append(entry)
        if not self._redo:
            self.can_redo_changed.emit(False)
        if not was_undoable:
            self.can_undo_changed.emit(True)
        return entry.forward

    def clear(self) -> None:
        had_u = bool(self._undo)
        had_r = bool(self._redo)
        self._undo.clear()
        self._redo.clear()
        if had_u:
            self.can_undo_changed.emit(False)
        if had_r:
            self.can_redo_changed.emit(False)
