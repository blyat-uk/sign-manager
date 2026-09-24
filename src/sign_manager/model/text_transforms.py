"""Pure text-transform utilities for label text.

Each transform takes ASS rich text (with inline ``{\\b1}``/``{\\i1}`` override
blocks) and returns new rich text with bold/italic boundaries preserved. The
pipeline is always:

    rich_text -> parse_rich_text -> per-segment mutation -> segments_to_ass

Six transforms are supported (see :class:`TransformKind`). The caller is
expected to derive the display ``text`` by stripping override blocks from the
returned rich text.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Callable

from sign_manager.geometry.rich_text import parse_rich_text, segments_to_ass
from sign_manager.model.text_segment import TextSegment


class TransformKind(Enum):
    SPACES_TO_BREAKS = "spaces_to_breaks"
    BREAKS_TO_SPACES = "breaks_to_spaces"
    BALANCE = "balance"
    BALANCE_3 = "balance_3"
    UPPERCASE = "uppercase"
    LOWERCASE = "lowercase"
    TITLE_CASE = "title_case"


# Capitalize the first letter of each whitespace-separated word. Unlike \b,
# this leaves apostrophes word-internal so "it's" -> "It's" (not "It'S").
_TITLE_BOUNDARY_RE = re.compile(r"(?:^|(?<=\s))[a-z]")


def _title_case(text: str) -> str:
    lowered = text.lower()
    return _TITLE_BOUNDARY_RE.sub(lambda m: m.group().upper(), lowered)


def _map_text(segments: list[TextSegment], fn: Callable[[str], str]) -> list[TextSegment]:
    return [TextSegment(fn(s.text), s.bold, s.italic) for s in segments]


def _balance(segments: list[TextSegment], lines: int = 2) -> list[TextSegment]:
    # Collapse any pre-existing line breaks so balance is idempotent.
    flat = [TextSegment(s.text.replace("\\N", " "), s.bold, s.italic) for s in segments]
    joined = "".join(s.text for s in flat)
    space_idxs = [i for i, ch in enumerate(joined) if ch == " "]
    if not space_idxs or lines < 2:
        return flat
    # Prefer breaking after a comma when commas exist; otherwise balance on any space.
    comma_space_idxs = [i for i in space_idxs if joined[i - 1] == ","]
    candidates = comma_space_idxs or space_idxs
    # Pick up to ``lines - 1`` split points greedily, each closest to its
    # equidistant target. Tie-break: prefer the smaller index (toward the start).
    total = len(joined)
    targets = [total * k // lines for k in range(1, lines)]
    chosen: list[int] = []
    pool = list(candidates)
    for target in targets:
        if not pool:
            break
        best = min(pool, key=lambda i: (abs(i - target), i))
        chosen.append(best)
        pool.remove(best)
    if not chosen:
        return flat
    chosen_sorted = sorted(chosen)
    out: list[TextSegment] = []
    running = 0
    for seg in flat:
        seg_len = len(seg.text)
        splits_here = [s for s in chosen_sorted if running <= s < running + seg_len]
        if not splits_here:
            out.append(seg)
        else:
            text = seg.text
            parts: list[str] = []
            pos = 0
            for s in splits_here:
                local = s - running
                parts.append(text[pos:local])
                parts.append("\\N")
                pos = local + 1
            parts.append(text[pos:])
            out.append(TextSegment("".join(parts), seg.bold, seg.italic))
        running += seg_len
    return out


def apply(
    rich_text: str,
    kind: TransformKind,
    *,
    default_bold: bool,
    default_italic: bool,
) -> str:
    """Return new rich text with ``kind`` applied.

    Display text (``label.text``) should be derived by stripping override
    blocks from the returned string — the caller already owns that regex.
    """
    if not rich_text:
        return rich_text

    segments = parse_rich_text(rich_text, default_bold, default_italic)

    if kind is TransformKind.SPACES_TO_BREAKS:
        new_segs = _map_text(segments, lambda t: t.replace(" ", "\\N"))
    elif kind is TransformKind.BREAKS_TO_SPACES:
        new_segs = _map_text(segments, lambda t: t.replace("\\N", " "))
    elif kind is TransformKind.BALANCE:
        new_segs = _balance(segments, lines=2)
    elif kind is TransformKind.BALANCE_3:
        new_segs = _balance(segments, lines=3)
    elif kind is TransformKind.UPPERCASE:
        new_segs = _map_text(segments, str.upper)
    elif kind is TransformKind.LOWERCASE:
        new_segs = _map_text(segments, str.lower)
    elif kind is TransformKind.TITLE_CASE:
        new_segs = _map_text(segments, _title_case)
    else:
        return rich_text

    return segments_to_ass(new_segs, default_bold, default_italic)
