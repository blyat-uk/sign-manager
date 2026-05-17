"""Tests for DragSession — pure drag/snap math."""

from PyQt6.QtCore import QPointF, QRectF, Qt

from sub_label_pos.geometry.drag import DragCandidate, DragSession
from sub_label_pos.model.types import LabelId


def _cands():
    # Place 'b' far enough on both axes that a pure-x drag of 'a' won't
    # accidentally snap on y (which would happen if both shared a y-edge).
    return [
        DragCandidate(label_id=LabelId("a"), rect=QRectF(100, 100, 50, 20)),
        DragCandidate(label_id=LabelId("b"), rect=QRectF(300, 500, 50, 20)),
    ]


def test_single_label_drag_no_snap_when_far():
    s = DragSession(
        initial_mouse=QPointF(110, 110),
        selected={LabelId("a")},
        all_candidates=_cands(),
        snap_threshold=6.0,
    )
    # Drag 'a' significantly to the right (way past any snap target)
    r = s.update(QPointF(500, 110), Qt.KeyboardModifier.NoModifier)
    assert r.deltas == {LabelId("a"): QPointF(390, 0)}
    # No guide because we're not near 'b'
    assert r.guides == []


def test_single_label_drag_snaps_to_other_edge_when_near():
    """Drag 'a' so its left edge approaches 'b's left edge — should snap."""
    s = DragSession(
        initial_mouse=QPointF(110, 110),
        selected={LabelId("a")},
        all_candidates=_cands(),
        snap_threshold=6.0,
    )
    # 'a' starts at left=100; moving by 198 puts left at 298 — 2px from 'b's left (300).
    r = s.update(QPointF(110 + 198, 110), Qt.KeyboardModifier.NoModifier)
    # Should snap left of 'a' to left of 'b' (300): final delta x = 300-100 = 200
    assert r.deltas[LabelId("a")].x() == 200
    assert r.guides  # at least one guide


def test_shift_disables_snap():
    s = DragSession(
        initial_mouse=QPointF(110, 110),
        selected={LabelId("a")},
        all_candidates=_cands(),
        snap_threshold=6.0,
    )
    r = s.update(QPointF(110 + 198, 110), Qt.KeyboardModifier.ShiftModifier)
    # With Shift, no snap applied — delta is exactly 198
    assert r.deltas[LabelId("a")].x() == 198
    assert r.guides == []


def test_multi_select_moves_together():
    s = DragSession(
        initial_mouse=QPointF(110, 110),
        selected={LabelId("a"), LabelId("b")},
        all_candidates=_cands(),
        snap_threshold=6.0,
    )
    r = s.update(QPointF(120, 130), Qt.KeyboardModifier.NoModifier)
    # No "other" rects to snap to (both labels are selected)
    assert r.deltas[LabelId("a")] == r.deltas[LabelId("b")] == QPointF(10, 20)
    assert r.guides == []


def test_no_candidates_no_crash():
    """Dragging when there are no candidates should still work."""
    s = DragSession(
        initial_mouse=QPointF(0, 0),
        selected=set(),
        all_candidates=[],
        snap_threshold=6.0,
    )
    r = s.update(QPointF(100, 100), Qt.KeyboardModifier.NoModifier)
    assert r.deltas == {}
    assert r.guides == []
