"""ASS-to-widget coordinate mapping (single source of truth).

The ASS file has its own coordinate space (PlayResX x PlayResY). The video
frame is displayed in a widget of arbitrary size. Mapping preserves aspect
ratio - the widget letterboxes if its aspect differs from the ASS aspect.
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF


def scale_factor(ass: tuple[int, int], widget: tuple[int, int]) -> float:
    """Return the uniform scale from ASS pixels to widget pixels.

    Returns 0.0 if widget has zero size (avoids ZeroDivisionError in callers).
    """
    if widget[0] <= 0 or widget[1] <= 0:
        return 0.0
    return min(widget[0] / ass[0], widget[1] / ass[1])


def ass_to_widget(
    p: QPointF, ass: tuple[int, int], widget: tuple[int, int],
) -> QPointF:
    s = scale_factor(ass, widget)
    return QPointF(p.x() * s, p.y() * s)


def widget_to_ass(
    p: QPointF, ass: tuple[int, int], widget: tuple[int, int],
) -> QPointF:
    s = scale_factor(ass, widget)
    if s == 0.0:
        return QPointF(0.0, 0.0)
    return QPointF(p.x() / s, p.y() / s)
