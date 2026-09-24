"""Pure frame ↔ seconds conversion and snap helpers.

All public functions are total: never raise; out-of-range inputs are clamped
or coerced. `fps <= 0` is the sentinel for "no fps available" (e.g., before
the video probe finishes) and degrades frame-snap operations gracefully to
centisecond-only snapping.

Spec: docs/superpowers/specs/2026-05-18-retiming-labels-design.md
"""

from __future__ import annotations


def snap_to_centisecond(seconds: float) -> float:
    """Round to the nearest 0.01 s — matches ASS file storage precision."""
    return round(seconds, 2)


def snap_to_frame(seconds: float, fps: float) -> float:
    """Round to the nearest frame boundary, then to the nearest centisecond.

    Returns the value that will be written to the .ass file.
    When ``fps <= 0``, only centisecond snapping is applied.
    """
    if fps <= 0:
        return snap_to_centisecond(seconds)
    frame = round(seconds * fps)
    return snap_to_centisecond(frame / fps)


def seconds_to_frame(seconds: float, fps: float) -> int:
    """Convert seconds to the closest integer frame index.

    Returns 0 when ``fps <= 0``.
    """
    if fps <= 0:
        return 0
    return int(round(seconds * fps))


def frame_to_seconds(frame: int, fps: float) -> float:
    """Convert frame index to seconds, snapped to centisecond.

    Returns 0.0 when ``fps <= 0``.
    """
    if fps <= 0:
        return 0.0
    return snap_to_centisecond(frame / fps)
