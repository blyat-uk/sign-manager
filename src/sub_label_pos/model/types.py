"""Foundational types for the label model: IDs, snapshots, style patches."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, fields, replace
from typing import NewType, TYPE_CHECKING

LabelId = NewType("LabelId", str)


@dataclass(frozen=True)
class StylePatch:
    """A partial style update. None fields are left unchanged when applied."""

    font_size: float | None = None
    primary_colour: str | None = None
    outline_colour: str | None = None
    outline_width: float | None = None
    bold: bool | None = None
    italic: bool | None = None
    font_name: str | None = None
    alignment: int | None = None
    rotation: float | None = None

    def merge(self, other: "StylePatch") -> "StylePatch":
        """Return a patch where ``other``'s non-None fields override ours."""
        updates = {
            f.name: getattr(other, f.name)
            for f in fields(other)
            if getattr(other, f.name) is not None
        }
        return replace(self, **updates)


if TYPE_CHECKING:
    from sub_label_pos.model.ass_file import LabelDialogue


@dataclass(frozen=True)
class LabelSnapshot:
    """Complete record of a label, sufficient to reinsert it identically."""

    label_id: LabelId
    dialogue: "LabelDialogue"      # frozen copy
    line_index: int                # original position in AssFile.lines


def new_label_id() -> LabelId:
    """Generate a fresh unique LabelId (UUID-based; prefix 'LD-')."""
    return LabelId(f"LD-{uuid.uuid4().hex[:12]}")
