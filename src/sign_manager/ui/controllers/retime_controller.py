"""RetimeController — orchestrates frame-precise label retiming.

Composes ``LabelEditController.retime_each`` to apply per-label updates with
multi-label semantics:

- ``set_in_at_current`` / ``set_out_at_current`` / ``set_in_to`` / ``set_out_to``:
  ALIGN each selected label's same edge to the target.
- ``nudge_start`` / ``nudge_end``: each selected label's same edge shifts by
  ±N frames.
- ``nudge_both`` / ``shift``: each selected label shifts (start AND end)
  by the same delta — preserves per-label duration.
- ``begin_drag`` / ``update_drag`` / ``end_drag``: lifecycle for handle drag.
  All ticks within a drag session coalesce into one undo entry.

A universal clamp rule applies after every operation: for each affected
label, ``end`` is forced to be at least ``start + frame_to_seconds(1, fps)``.

Spec: docs/superpowers/specs/2026-05-18-retiming-labels-design.md
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Literal

from sign_manager.geometry.frame_time import frame_to_seconds, snap_to_frame
from sign_manager.geometry.time_input import parse_time_input
from sign_manager.model.label_store import LabelStore
from sign_manager.model.types import LabelId
from sign_manager.ui.controllers.label_edit_controller import LabelEditController


class RetimeController:
    def __init__(
        self,
        store: LabelStore,
        edit: LabelEditController,
        *,
        fps_provider: Callable[[], float],
        current_time_provider: Callable[[], float],
        seek_callback: Callable[[float], None] | None = None,
    ) -> None:
        self._store = store
        self._edit = edit
        self._fps = fps_provider
        self._now = current_time_provider
        self._seek = seek_callback or (lambda _t: None)
        self._drag_session: str | None = None
        self._drag_edge: Literal["start", "end"] | None = None

    # --- Mark in/out at current playhead time ---

    def set_in_at_current(self) -> None:
        self.set_in_to(self._now())

    def set_out_at_current(self) -> None:
        self.set_out_to(self._now())

    # --- Seek the player to the in / out edge of the selection ---

    def seek_to_in(self) -> None:
        """Seek the player to the earliest start_time across the selection.

        Useful for confirming the label's first frame is exactly where the
        user wants it to appear."""
        labels = self._selected_label_dialogues()
        if not labels:
            return
        self._seek(min(lb.start_time for lb in labels))

    def seek_to_out(self) -> None:
        """Seek the player to the latest end_time across the selection.

        Useful for confirming the label's last frame is exactly where the
        user wants it to disappear."""
        labels = self._selected_label_dialogues()
        if not labels:
            return
        self._seek(max(lb.end_time for lb in labels))

    # --- Explicit set ---

    def set_in_to(self, t: float) -> None:
        fps = self._fps()
        t = snap_to_frame(max(0.0, t), fps)
        updates = []
        for lid in self._selected():
            lb = self._store.state.labels[lid]
            new_start, new_end = self._clamp(t, lb.end_time, fps)
            updates.append((lid, new_start, new_end))
        self._edit.retime_each(updates)

    def set_out_to(self, t: float) -> None:
        fps = self._fps()
        t = snap_to_frame(max(0.0, t), fps)
        updates = []
        for lid in self._selected():
            lb = self._store.state.labels[lid]
            new_start, new_end = self._clamp(lb.start_time, t, fps)
            updates.append((lid, new_start, new_end))
        self._edit.retime_each(updates)

    # --- Frame-step nudge ---

    def nudge_start(self, frames: int) -> None:
        fps = self._fps()
        delta = frames / fps if fps > 0 else 0.0
        updates = []
        for lid in self._selected():
            lb = self._store.state.labels[lid]
            new_start = snap_to_frame(max(0.0, lb.start_time + delta), fps)
            new_start, new_end = self._clamp(new_start, lb.end_time, fps)
            updates.append((lid, new_start, new_end))
        self._edit.retime_each(updates)

    def nudge_end(self, frames: int) -> None:
        fps = self._fps()
        delta = frames / fps if fps > 0 else 0.0
        updates = []
        for lid in self._selected():
            lb = self._store.state.labels[lid]
            new_end = snap_to_frame(max(0.0, lb.end_time + delta), fps)
            new_start, new_end = self._clamp(lb.start_time, new_end, fps)
            updates.append((lid, new_start, new_end))
        self._edit.retime_each(updates)

    def nudge_both(self, frames: int) -> None:
        fps = self._fps()
        delta = frames / fps if fps > 0 else 0.0
        self.shift(delta)

    # --- Bulk shift from numeric input (preserves duration) ---

    def shift(self, delta_seconds: float) -> None:
        fps = self._fps()
        updates = []
        for lid in self._selected():
            lb = self._store.state.labels[lid]
            duration = lb.end_time - lb.start_time
            new_start = snap_to_frame(max(0.0, lb.start_time + delta_seconds), fps)
            new_end = snap_to_frame(new_start + duration, fps)
            new_start, new_end = self._clamp(new_start, new_end, fps)
            updates.append((lid, new_start, new_end))
        self._edit.retime_each(updates)

    # --- Numeric input from RetimeBar's Start/End fields ---

    def apply_input_to_start(self, text: str) -> bool:
        fps = self._fps()
        ids = list(self._selected())
        if not ids:
            return True   # empty selection: no-op, not a parse failure
        updates = []
        for lid in ids:
            lb = self._store.state.labels[lid]
            t = parse_time_input(text, current=lb.start_time, fps=fps)
            if t is None:
                return False
            new_start, new_end = self._clamp(t, lb.end_time, fps)
            updates.append((lid, new_start, new_end))
        self._edit.retime_each(updates)
        return True

    def apply_input_to_end(self, text: str) -> bool:
        fps = self._fps()
        ids = list(self._selected())
        if not ids:
            return True   # empty selection: no-op, not a parse failure
        updates = []
        for lid in ids:
            lb = self._store.state.labels[lid]
            t = parse_time_input(text, current=lb.end_time, fps=fps)
            if t is None:
                return False
            new_start, new_end = self._clamp(lb.start_time, t, fps)
            updates.append((lid, new_start, new_end))
        self._edit.retime_each(updates)
        return True

    # --- Drag lifecycle (called by FocusedTimeline) ---

    def begin_drag(self, edge: Literal["start", "end"]) -> None:
        self._drag_session = f"retime-drag:{uuid.uuid4().hex[:12]}"
        self._drag_edge = edge

    def update_drag(self, t: float) -> None:
        if self._drag_session is None or self._drag_edge is None:
            return
        fps = self._fps()
        t = snap_to_frame(max(0.0, t), fps)
        updates = []
        for lid in self._selected():
            lb = self._store.state.labels[lid]
            if self._drag_edge == "start":
                ns, ne = self._clamp(t, lb.end_time, fps)
            else:
                ns, ne = self._clamp(lb.start_time, t, fps)
            updates.append((lid, ns, ne))
        if not updates:
            return
        self._edit.retime_each(updates, coalesce_key=self._drag_session)

    def end_drag(self) -> None:
        self._drag_session = None
        self._drag_edge = None

    # --- Public accessors for the UI ---

    def fps(self) -> float:
        """Current video fps (0.0 if no video loaded)."""
        return self._fps()

    # --- Internal helpers ---

    def _selected(self) -> set[LabelId]:
        return set(self._store.selected)

    def _selected_label_dialogues(self) -> list:
        """Return LabelDialogue instances for currently-selected labels that
        still exist in the store. Used by seek_to_in / seek_to_out."""
        return [
            self._store.state.labels[lid]
            for lid in self._store.selected
            if lid in self._store.state.labels
        ]

    def _clamp(
        self, start: float, end: float, fps: float,
    ) -> tuple[float, float]:
        """Ensure end is at least one frame past start; snap both to frame.

        When fps > 200 (so 1/fps rounds to less than one centisecond), the
        minimum-duration step falls back to one centisecond — the finest
        representable step at ASS file precision.
        """
        start = snap_to_frame(max(0.0, start), fps)
        end = snap_to_frame(max(0.0, end), fps)
        min_dur = frame_to_seconds(1, fps) if fps > 0 else 0.01
        if min_dur == 0.0:
            min_dur = 0.01
        if end <= start:
            end = snap_to_frame(start + min_dur, fps)
            # snap_to_frame might round back down equal to start at certain fps;
            # add another frame increment until strictly greater.
            while end <= start:
                end = snap_to_frame(end + min_dur, fps)
        return start, end
