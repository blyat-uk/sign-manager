"""Pure functions for computing label render geometry.

These functions are independent of QWidget — they take font/style/position/size
inputs and return widget-space rects. The libass font correction factor is
applied here so positions in the editor match positions in libass-rendered
playback (mpv etc.).

The math is preserved byte-for-byte from the original
VideoFrameWidget._compute_rect / _libass_font_correction implementation.
"""

from __future__ import annotations

import struct
from typing import Callable

from PyQt6.QtCore import QPointF, QRectF
from PyQt6.QtGui import QFont, QFontMetricsF, QRawFont

from sub_label_pos.geometry.rich_text import parse_rich_text
from sub_label_pos.model.ass_file import LabelDialogue, TextSegment


def libass_font_correction(font: QFont) -> float:
    """Correction factor so Qt's setPixelSize matches libass rendering.

    libass overrides FreeType face metrics with OS/2 usWinAscent/usWinDescent
    and uses FT_SIZE_REQUEST_TYPE_REAL_DIM, mapping font size to the Win cell
    height.  Qt's setPixelSize maps to the em-square.  This returns
    unitsPerEm / (usWinAscent + usWinDescent).
    """
    raw = QRawFont.fromFont(font)
    os2 = raw.fontTable(b"OS/2")
    if len(os2) < 78:
        return 1.0
    upm = raw.unitsPerEm()
    win_asc = struct.unpack_from(">H", os2, 74)[0]
    win_desc = struct.unpack_from(">H", os2, 76)[0]
    cell = win_asc + win_desc
    return upm / cell if cell > 0 else 1.0


def compute_label_rect(
    label: LabelDialogue,
    *,
    default_alignment: int,
    default_bold: bool,
    default_italic: bool,
    font_provider: Callable[[bool | None, bool | None], QFont],
    ass_pos_to_widget: Callable[[int, int], QPointF],
) -> QRectF:
    """Return the widget-space rect for a label.

    Pure function — no widget references. Behavior preserved byte-for-byte
    from VideoFrameWidget._compute_rect.

    Parameters
    ----------
    label:
        The label being measured.
    default_alignment:
        The numpad alignment (1-9) used when ``label.alignment`` is None.
    default_bold, default_italic:
        Defaults applied when parsing the rich text into segments.
    font_provider:
        Given ``(bold_override, italic_override)``, return a fully-prepared
        ``QFont`` (with libass correction + pixel-scale already applied) for
        measuring a segment of this label.  Pass ``None`` for either override
        to use the label's own style defaults.
    ass_pos_to_widget:
        Maps the label's ASS-space ``(pos_x, pos_y)`` to widget-space
        coordinates (including any letterbox offset).
    """
    segments = parse_rich_text(label.rich_text, default_bold, default_italic)

    # Split segments by \N into lines of segments
    seg_lines: list[list[TextSegment]] = [[]]
    for seg in segments:
        parts = seg.text.split("\\N")
        for i, part in enumerate(parts):
            if i > 0:
                seg_lines.append([])
            if part:
                seg_lines[-1].append(TextSegment(part, seg.bold, seg.italic))

    base_font = font_provider(None, None)
    max_w = 0.0
    max_line_h = 0.0
    for seg_line in seg_lines:
        line_w = 0.0
        line_h = 0.0
        for seg in seg_line:
            seg_font = font_provider(seg.bold, seg.italic)
            seg_fm = QFontMetricsF(seg_font)
            line_w += seg_fm.horizontalAdvance(seg.text)
            line_h = max(line_h, seg_fm.height())
        if not seg_line:
            line_h = QFontMetricsF(base_font).height()
        max_w = max(max_w, line_w)
        max_line_h = max(max_line_h, line_h)

    pad_x, pad_y = 6, 4
    total_w = max_w + 2 * pad_x
    total_h = max_line_h * len(seg_lines) + 2 * pad_y
    pos = ass_pos_to_widget(label.pos_x, label.pos_y)
    alignment = label.alignment if label.alignment is not None else default_alignment

    h_align = ((alignment - 1) % 3) + 1
    v_group = (alignment - 1) // 3

    if h_align == 1:
        x = pos.x()
    elif h_align == 3:
        x = pos.x() - total_w
    else:
        x = pos.x() - total_w / 2

    if v_group == 0:  # bottom
        y = pos.y() - total_h
    elif v_group == 1:  # middle
        y = pos.y() - total_h / 2
    else:  # top
        y = pos.y()

    return QRectF(x, y, total_w, total_h)
