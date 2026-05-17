"""Pure drag + snap logic. No QWidget references; only QPointF/QRectF."""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QPointF, QRectF, Qt

from sub_label_pos.model.types import LabelId


@dataclass(frozen=True)
class DragCandidate:
    """A label that exists in the current view. Used by DragSession to
    compute snap targets (selected) and snap sources (non-selected)."""

    label_id: LabelId
    rect: QRectF  # widget-space


@dataclass(frozen=True)
class SnapGuide:
    """A visual guide line to render during drag.

    axis: "x" (vertical line at value=x) or "y" (horizontal line at value=y).
    span_min/span_max delimit how far the line extends along the orthogonal axis.
    """

    axis: str
    value: float
    span_min: float
    span_max: float


@dataclass(frozen=True)
class SnapResult:
    """Output of DragSession.update(): per-label deltas + guides to render."""

    deltas: dict[LabelId, QPointF]
    guides: list[SnapGuide]


class DragSession:
    """One drag interaction, scoped to a single mouse-down -> mouse-up.

    Snapping aligns left/center/right edges and top/middle/bottom edges of the
    selected labels to the corresponding edges of non-selected labels within
    `snap_threshold` pixels. Shift modifier disables snapping.
    """

    def __init__(
        self,
        *,
        initial_mouse: QPointF,
        selected: set[LabelId],
        all_candidates: list[DragCandidate],
        snap_threshold: float = 6.0,
    ) -> None:
        self._initial_mouse = initial_mouse
        self._selected = set(selected)
        self._threshold = snap_threshold
        self._initial_rects: dict[LabelId, QRectF] = {
            c.label_id: c.rect for c in all_candidates if c.label_id in selected
        }
        self._other_rects: list[QRectF] = [
            c.rect for c in all_candidates if c.label_id not in selected
        ]

    def update(self, mouse: QPointF, modifiers: Qt.KeyboardModifier) -> SnapResult:
        """Recompute deltas + guides given the current mouse position."""
        dx_raw = mouse.x() - self._initial_mouse.x()
        dy_raw = mouse.y() - self._initial_mouse.y()
        snap_disabled = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        guides: list[SnapGuide] = []
        dx, dy = dx_raw, dy_raw
        if not snap_disabled and self._other_rects and self._initial_rects:
            dx, gx = self._snap_axis_x(dx_raw)
            dy, gy = self._snap_axis_y(dy_raw)
            if gx is not None:
                guides.append(gx)
            if gy is not None:
                guides.append(gy)
        delta = QPointF(dx, dy)
        deltas = {lid: delta for lid in self._selected}
        return SnapResult(deltas=deltas, guides=guides)

    def _candidate_xs(self) -> list[tuple[float, QRectF]]:
        """Each candidate edge of the selected group, paired with the selected rect."""
        out = []
        for rect in self._initial_rects.values():
            for x in (rect.left(), rect.center().x(), rect.right()):
                out.append((x, rect))
        return out

    def _candidate_ys(self) -> list[tuple[float, QRectF]]:
        out = []
        for rect in self._initial_rects.values():
            for y in (rect.top(), rect.center().y(), rect.bottom()):
                out.append((y, rect))
        return out

    def _other_xs(self) -> list[float]:
        return [
            v
            for r in self._other_rects
            for v in (r.left(), r.center().x(), r.right())
        ]

    def _other_ys(self) -> list[float]:
        return [
            v
            for r in self._other_rects
            for v in (r.top(), r.center().y(), r.bottom())
        ]

    def _snap_axis_x(self, dx: float) -> tuple[float, SnapGuide | None]:
        """Find the smallest adjustment that aligns a selected edge to an other edge."""
        best_adj = 0.0
        best_dist = self._threshold + 1.0
        best_target: float | None = None
        for sx, _src_rect in self._candidate_xs():
            for ox in self._other_xs():
                # selected edge after dx is sx+dx; want |(sx+dx) - ox| < threshold
                adj = ox - (sx + dx)
                dist = abs(adj)
                if dist < best_dist:
                    best_dist = dist
                    best_adj = adj
                    best_target = ox
        if best_dist > self._threshold or best_target is None:
            return dx, None
        guide = SnapGuide(
            axis="x",
            value=best_target,
            span_min=0.0,
            span_max=10_000.0,  # widget will clip
        )
        return dx + best_adj, guide

    def _snap_axis_y(self, dy: float) -> tuple[float, SnapGuide | None]:
        best_adj = 0.0
        best_dist = self._threshold + 1.0
        best_target: float | None = None
        for sy, _ in self._candidate_ys():
            for oy in self._other_ys():
                adj = oy - (sy + dy)
                dist = abs(adj)
                if dist < best_dist:
                    best_dist = dist
                    best_adj = adj
                    best_target = oy
        if best_dist > self._threshold or best_target is None:
            return dy, None
        guide = SnapGuide(
            axis="y",
            value=best_target,
            span_min=0.0,
            span_max=10_000.0,
        )
        return dy + best_adj, guide
