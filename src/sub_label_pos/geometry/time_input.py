"""Pure parser for retime numeric input fields.

Accepts absolute time strings (``H:MM:SS.cc``, ``M:SS.cc``, ``SS.cc``, ``SS``)
and relative deltas with units (``+250ms``, ``-1.5s``, ``+6f``). Total — never
raises; returns ``None`` on parse failure so the caller can show inline
feedback.

Spec: docs/superpowers/specs/2026-05-18-retiming-labels-design.md
"""

from __future__ import annotations

import re

from sub_label_pos.geometry.frame_time import snap_to_frame

_REL_RE = re.compile(r"^([+\-])\s*(\d+(?:\.\d+)?)\s*(ms|s|f)$", re.IGNORECASE)
_HMS_RE = re.compile(r"^(\d+):([0-5]?\d):([0-5]?\d)(?:\.(\d+))?$")
_MS_RE = re.compile(r"^(\d+):([0-5]?\d)(?:\.(\d+))?$")
_S_RE = re.compile(r"^(\d+)(?:\.(\d+))?$")


def parse_time_input(text: str, *, current: float, fps: float) -> float | None:
    """Parse a user input string into absolute seconds, snapped to frame + centisecond.

    Accepted forms (whitespace trimmed, case-insensitive units):
      Absolute:
        '0:01:23.45'   H:MM:SS.cc
        '1:23.45'      M:SS.cc
        '83.45'        SS.cc
        '83'           SS

      Relative (evaluated against ``current``):
        '+250ms', '-250ms'
        '+3s',    '-1.5s'
        '+6f',    '-12f'

    Returns the result snapped via ``snap_to_frame``, or ``None`` on parse failure.
    """
    if text is None:
        return None
    s = text.strip()
    if not s:
        return None

    # Relative form first — anything starting with + or - is relative.
    if s[0] in "+-":
        m = _REL_RE.match(s)
        if not m:
            return None
        sign, num_str, unit = m.group(1), m.group(2), m.group(3).lower()
        try:
            value = float(num_str)
        except ValueError:
            return None
        if unit == "ms":
            delta = value / 1000.0
        elif unit == "s":
            delta = value
        elif unit == "f":
            delta = (value / fps) if fps > 0 else 0.0
        else:
            return None
        if sign == "-":
            delta = -delta
        return snap_to_frame(max(0.0, current + delta), fps)

    # Absolute form: try H:MM:SS.cc, then M:SS.cc, then SS.cc, then SS.
    for regex, builder in (
        (_HMS_RE, _absolute_from_hms),
        (_MS_RE, _absolute_from_ms),
        (_S_RE, _absolute_from_s),
    ):
        m = regex.match(s)
        if m:
            seconds = builder(m)
            return snap_to_frame(max(0.0, seconds), fps)
    return None


def parse_relative_delta(text: str, *, fps: float) -> float | None:
    """Parse a relative time delta (e.g., '+250ms', '-6f', '+1.5s') into signed seconds.

    Unlike ``parse_time_input``, this never clamps to zero — the returned delta
    can be negative. Returns ``None`` if the text is not a valid relative form
    (anything not starting with '+' or '-', or with an unknown unit).

    The result is rounded to centisecond precision; frame units (``f``) require
    ``fps > 0`` and otherwise return ``None`` (so callers can show a meaningful
    error instead of silently producing a 0-second delta).
    """
    if text is None:
        return None
    s = text.strip()
    if not s or s[0] not in "+-":
        return None
    m = _REL_RE.match(s)
    if not m:
        return None
    sign, num_str, unit = m.group(1), m.group(2), m.group(3).lower()
    try:
        value = float(num_str)
    except ValueError:
        return None
    if unit == "ms":
        delta = value / 1000.0
    elif unit == "s":
        delta = value
    elif unit == "f":
        if fps <= 0:
            return None
        delta = value / fps
    else:
        return None
    if sign == "-":
        delta = -delta
    # Round to centisecond — same precision granularity as ASS storage.
    return round(delta, 2)


def _absolute_from_hms(m: re.Match) -> float:
    h, mm, ss = int(m.group(1)), int(m.group(2)), int(m.group(3))
    cc = _cc_from_group(m.group(4))
    return h * 3600 + mm * 60 + ss + cc


def _absolute_from_ms(m: re.Match) -> float:
    mm, ss = int(m.group(1)), int(m.group(2))
    cc = _cc_from_group(m.group(3))
    return mm * 60 + ss + cc


def _absolute_from_s(m: re.Match) -> float:
    ss = int(m.group(1))
    cc = _cc_from_group(m.group(2))
    return ss + cc


def _cc_from_group(group: str | None) -> float:
    """Parse the decimal portion (any number of digits) into a float fraction."""
    if not group:
        return 0.0
    # Convert directly to float — "456" becomes 0.456, "45" becomes 0.45, etc.
    return float("0." + group)
