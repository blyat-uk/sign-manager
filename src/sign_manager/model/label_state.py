"""Mutable, in-memory label state. Pure data — no signals, no Qt."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sign_manager.model.types import LabelId, LabelSnapshot

if TYPE_CHECKING:
    from sign_manager.model.ass_file import AssStyle, LabelDialogue


@dataclass
class LabelState:
    """Authoritative mutable state for all labels in a file.

    Maintains both a dict (fast lookup by id) and an order list (preserves
    on-disk line order). Provides primitive insert/remove/replace operations
    used by Mutation.apply implementations.
    """

    labels: dict[LabelId, "LabelDialogue"] = field(default_factory=dict)
    order: list[LabelId] = field(default_factory=list)
    styles: dict[str, "AssStyle"] = field(default_factory=dict)

    def insert(self, lid: LabelId, dialogue: "LabelDialogue", index: int) -> None:
        if lid in self.labels:
            raise ValueError(f"label {lid} already exists")
        self.labels[lid] = dialogue
        self.order.insert(index, lid)

    def remove(self, lid: LabelId) -> LabelSnapshot:
        if lid not in self.labels:
            raise KeyError(lid)
        idx = self.order.index(lid)
        dlg = self.labels.pop(lid)
        self.order.remove(lid)
        return LabelSnapshot(label_id=lid, dialogue=dlg, line_index=idx)

    def replace(self, lid: LabelId, dialogue: "LabelDialogue") -> "LabelDialogue":
        if lid not in self.labels:
            raise KeyError(lid)
        old = self.labels[lid]
        self.labels[lid] = dialogue
        return old

    def index_of(self, lid: LabelId) -> int:
        return self.order.index(lid)
