"""Mutation Protocol and BatchMutation.

Each concrete mutation (MoveLabel, ResizeLabel, etc.) lives in this module too
and implements the Mutation Protocol. ``LabelStore.apply(mutation)`` runs the
mutation against state and emits typed signals.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Iterable, Protocol, runtime_checkable

from sub_label_pos.model.label_state import LabelState
from sub_label_pos.model.types import LabelId, LabelSnapshot, StylePatch


@runtime_checkable
class Mutation(Protocol):
    """A mutation knows how to apply itself and how to invert itself.

    coalesce_key: when two mutations with the same non-None key are pushed onto
    the UndoStack within the coalescing window, they collapse into one undo
    entry. None means never coalesce.
    """

    coalesce_key: str | None

    def apply(self, state: LabelState) -> set[LabelId]:
        """Mutate state in place. Return the label ids affected."""
        ...

    def invert(self, state_before: LabelState) -> "Mutation":
        """Return the mutation that, applied after this one, restores the prior state.

        The ``state_before`` is the state as it exists BEFORE this mutation has
        been applied; the caller is responsible for snapshotting it.
        """
        ...


@dataclass(frozen=True)
class BatchMutation:
    """Atomic batch of mutations. One apply, one emission, one undo entry.

    Constraint: inner mutations should not depend on each other's pre-state for
    inversion (each inner.invert is computed against the same ``state_before``).
    Compose batches accordingly — for compositions with intra-batch dependencies,
    build separate batches.
    """

    inner: tuple = field(default_factory=tuple)
    coalesce_key = None    # class attribute, not a dataclass field

    def __init__(self, inner: Iterable):
        # __init__ is custom so callers can pass any iterable
        object.__setattr__(self, "inner", tuple(inner))

    def apply(self, state: LabelState) -> set[LabelId]:
        affected: set[LabelId] = set()
        for m in self.inner:
            affected |= m.apply(state)
        return affected

    def invert(self, state_before: LabelState) -> "BatchMutation":
        return BatchMutation(tuple(m.invert(state_before) for m in reversed(self.inner)))


@dataclass(frozen=True)
class MoveLabel:
    """Reposition a label."""
    label_id: LabelId
    new_x: float
    new_y: float

    @property
    def coalesce_key(self) -> str:
        return f"move:{self.label_id}"

    def apply(self, state: LabelState) -> set[LabelId]:
        dlg = state.labels[self.label_id]
        state.labels[self.label_id] = replace(dlg, pos_x=self.new_x, pos_y=self.new_y)
        return {self.label_id}

    def invert(self, state_before: LabelState) -> "MoveLabel":
        old = state_before.labels[self.label_id]
        return MoveLabel(label_id=self.label_id, new_x=old.pos_x, new_y=old.pos_y)


@dataclass(frozen=True)
class ResizeLabel:
    """Change a label's font size."""
    label_id: LabelId
    new_font_size: int

    @property
    def coalesce_key(self) -> str:
        return f"resize:{self.label_id}"

    def apply(self, state: LabelState) -> set[LabelId]:
        dlg = state.labels[self.label_id]
        state.labels[self.label_id] = replace(dlg, font_size=self.new_font_size)
        return {self.label_id}

    def invert(self, state_before: LabelState) -> "ResizeLabel":
        old = state_before.labels[self.label_id]
        return ResizeLabel(label_id=self.label_id, new_font_size=old.font_size)


@dataclass(frozen=True)
class RotateLabel:
    """Change a label's rotation angle (degrees)."""
    label_id: LabelId
    new_angle: float

    @property
    def coalesce_key(self) -> str:
        return f"rotate:{self.label_id}"

    def apply(self, state: LabelState) -> set[LabelId]:
        dlg = state.labels[self.label_id]
        state.labels[self.label_id] = replace(dlg, rotation=self.new_angle)
        return {self.label_id}

    def invert(self, state_before: LabelState) -> "RotateLabel":
        old = state_before.labels[self.label_id]
        return RotateLabel(label_id=self.label_id, new_angle=old.rotation)


@dataclass(frozen=True)
class EditText:
    """Change a label's text and rich_text atomically.

    The widget is responsible for computing both new_text (display, no inline
    blocks) and new_rich_text (with inline formatting blocks) before submitting.
    They must be consistent — this mutation just stores them.
    """
    label_id: LabelId
    new_text: str
    new_rich_text: str

    @property
    def coalesce_key(self) -> str:
        return f"edit_text:{self.label_id}"

    def apply(self, state: LabelState) -> set[LabelId]:
        dlg = state.labels[self.label_id]
        state.labels[self.label_id] = replace(
            dlg, text=self.new_text, rich_text=self.new_rich_text,
        )
        return {self.label_id}

    def invert(self, state_before: LabelState) -> "EditText":
        old = state_before.labels[self.label_id]
        return EditText(
            label_id=self.label_id,
            new_text=old.text,
            new_rich_text=old.rich_text,
        )


# Names of LabelDialogue fields that a StylePatch can update.
# font_name is intentionally excluded — LabelDialogue does not currently
# carry a per-label font override; styles are referenced by name.
_STYLE_PATCH_LABEL_FIELDS = (
    "font_size", "primary_colour", "outline_colour", "outline_width",
    "bold", "italic", "alignment", "rotation",
)


@dataclass(frozen=True)
class ChangeStyle:
    """Apply a partial style update.

    The set of fields that this mutation touches is determined by the patch's
    non-None fields at construction time, and recorded on ``_active_fields``.
    The inverse must be able to restore prior values that were themselves None,
    so the active-field set is tracked explicitly rather than re-derived from
    the inverse patch (whose values may legitimately be None).
    """
    label_id: LabelId
    patch: StylePatch
    # Explicit set of LabelDialogue field names to write. If None at construction,
    # it is inferred from the patch's non-None fields (excluding font_name).
    _active_fields: tuple[str, ...] | None = None
    coalesce_key = None    # class attribute (no coalescing)

    def _fields_to_apply(self) -> tuple[str, ...]:
        if self._active_fields is not None:
            return self._active_fields
        return tuple(
            f for f in _STYLE_PATCH_LABEL_FIELDS
            if getattr(self.patch, f) is not None
        )

    def apply(self, state: LabelState) -> set[LabelId]:
        dlg = state.labels[self.label_id]
        active = self._fields_to_apply()
        updates = {f: getattr(self.patch, f) for f in active}
        if updates:
            state.labels[self.label_id] = replace(dlg, **updates)
        else:
            # Still produce a new instance to be consistent with other mutations.
            state.labels[self.label_id] = replace(dlg)
        return {self.label_id}

    def invert(self, state_before: LabelState) -> "ChangeStyle":
        old = state_before.labels[self.label_id]
        active = self._fields_to_apply()
        inv_kwargs = {f: getattr(old, f) for f in active}
        return ChangeStyle(
            label_id=self.label_id,
            patch=StylePatch(**inv_kwargs),
            _active_fields=active,
        )


class PasteStyle(ChangeStyle):
    """Alias of ChangeStyle for widget-side semantic clarity (paste-style action)."""
    pass


@dataclass(frozen=True)
class RetimeLabel:
    """Change a label's start and end time (seconds).

    coalesce_key: by default ``f"retime:{label_id}"`` so consecutive retimes of
    the same label within the undo-stack's coalesce window collapse. Pass
    ``coalesce_key_override`` (e.g., a per-drag-session key) to override.
    """
    label_id: LabelId
    new_start: float
    new_end: float
    coalesce_key_override: str | None = None

    @property
    def coalesce_key(self) -> str:
        if self.coalesce_key_override is not None:
            return self.coalesce_key_override
        return f"retime:{self.label_id}"

    def apply(self, state: LabelState) -> set[LabelId]:
        dlg = state.labels[self.label_id]
        state.labels[self.label_id] = replace(
            dlg, start_time=self.new_start, end_time=self.new_end,
        )
        return {self.label_id}

    def invert(self, state_before: LabelState) -> "RetimeLabel":
        old = state_before.labels[self.label_id]
        return RetimeLabel(
            label_id=self.label_id,
            new_start=old.start_time,
            new_end=old.end_time,
        )


@dataclass(frozen=True)
class DeleteLabel:
    """Remove a label. Carries a snapshot for restoration on undo."""
    label_id: LabelId
    snapshot: LabelSnapshot
    coalesce_key = None    # class attribute

    @classmethod
    def from_state(cls, state: LabelState, label_id: LabelId) -> "DeleteLabel":
        """Construct from current state, capturing a snapshot before deletion."""
        dlg = state.labels[label_id]
        idx = state.index_of(label_id)
        snap = LabelSnapshot(label_id=label_id, dialogue=dlg, line_index=idx)
        return cls(label_id=label_id, snapshot=snap)

    def apply(self, state: LabelState) -> set[LabelId]:
        state.remove(self.label_id)
        return {self.label_id}

    def invert(self, state_before: LabelState) -> "InsertLabel":
        return InsertLabel(snapshot=self.snapshot)


@dataclass(frozen=True)
class InsertLabel:
    """Insert a label from a snapshot. Inverse of DeleteLabel."""
    snapshot: LabelSnapshot
    coalesce_key = None

    def apply(self, state: LabelState) -> set[LabelId]:
        index = min(self.snapshot.line_index, len(state.order))
        state.insert(self.snapshot.label_id, self.snapshot.dialogue, index=index)
        return {self.snapshot.label_id}

    def invert(self, state_before: LabelState) -> DeleteLabel:
        return DeleteLabel(label_id=self.snapshot.label_id, snapshot=self.snapshot)


@dataclass(frozen=True)
class DuplicateLabel:
    """Duplicate a label at a new id, inserted after the source with a position offset."""
    source_label_id: LabelId
    new_label_id: LabelId
    position_offset: tuple[float, float] = (20.0, 20.0)
    coalesce_key = None

    def apply(self, state: LabelState) -> set[LabelId]:
        src = state.labels[self.source_label_id]
        dx, dy = self.position_offset
        # Note: pos_x/pos_y are ints in the model; coerce to keep types consistent.
        new_dlg = replace(
            src,
            label_id=self.new_label_id,
            pos_x=int(src.pos_x + dx),
            pos_y=int(src.pos_y + dy),
        )
        src_index = state.index_of(self.source_label_id)
        state.insert(self.new_label_id, new_dlg, index=src_index + 1)
        return {self.new_label_id}

    def invert(self, state_before: LabelState) -> "DeleteLabel":
        # state_before has no new label; synthesize a snapshot of what apply WILL create
        src = state_before.labels[self.source_label_id]
        dx, dy = self.position_offset
        new_dlg = replace(
            src,
            label_id=self.new_label_id,
            pos_x=int(src.pos_x + dx),
            pos_y=int(src.pos_y + dy),
        )
        src_index = state_before.index_of(self.source_label_id)
        snap = LabelSnapshot(
            label_id=self.new_label_id,
            dialogue=new_dlg,
            line_index=src_index + 1,
        )
        return DeleteLabel(label_id=self.new_label_id, snapshot=snap)


@dataclass(frozen=True)
class MergeLabels:
    """Merge N source labels into a single combined label.

    The widget is responsible for computing ``merged_dialogue`` (text union,
    time union, style choice) before submitting. The mutation just executes
    the structural change atomically.
    """
    source_snapshots: tuple[LabelSnapshot, ...]
    merged_id: LabelId
    merged_dialogue: object   # LabelDialogue with label_id == merged_id
    coalesce_key = None

    def apply(self, state: LabelState) -> set[LabelId]:
        # Compute the earliest line_index across the sources for placement.
        first_idx = min(s.line_index for s in self.source_snapshots)
        # Remove sources in descending index order so prior indices stay valid.
        for s in sorted(self.source_snapshots, key=lambda x: -x.line_index):
            state.remove(s.label_id)
        # Insert the merged label at the earliest original index (clamped).
        state.insert(
            self.merged_id, self.merged_dialogue,
            index=min(first_idx, len(state.order)),
        )
        return {self.merged_id} | {s.label_id for s in self.source_snapshots}

    def invert(self, state_before: LabelState) -> "SplitMerged":
        return SplitMerged(
            source_snapshots=self.source_snapshots, merged_id=self.merged_id,
        )


@dataclass(frozen=True)
class SplitMerged:
    """Inverse of MergeLabels: remove the merged label, restore the sources.

    Limitation: ``invert()`` cannot perfectly reconstruct the original merged
    dialogue (text union, time union, style choices computed by the widget).
    It uses the first source's dialogue as a template, so a
    redo-after-undo of a split-merged sequence may lose merged-label
    customizations. Acceptable for the v1 undo system. If true round-trip is
    needed, callers should snapshot the merged dialogue before applying
    SplitMerged and construct a MergeLabels with it explicitly.
    """
    source_snapshots: tuple[LabelSnapshot, ...]
    merged_id: LabelId
    coalesce_key = None

    def apply(self, state: LabelState) -> set[LabelId]:
        state.remove(self.merged_id)
        # Reinsert in ascending line_index so positions are stable
        for s in sorted(self.source_snapshots, key=lambda x: x.line_index):
            state.insert(
                s.label_id, s.dialogue,
                index=min(s.line_index, len(state.order)),
            )
        return {self.merged_id} | {s.label_id for s in self.source_snapshots}

    def invert(self, state_before: LabelState) -> MergeLabels:
        # state_before HAS the merged label but not the sources. We can't
        # perfectly reconstruct the original widget-computed merged dialogue;
        # synthesize from the first source's snapshot. See class docstring.
        first = self.source_snapshots[0].dialogue
        merged_dlg = replace(first, label_id=self.merged_id)
        return MergeLabels(
            source_snapshots=self.source_snapshots,
            merged_id=self.merged_id,
            merged_dialogue=merged_dlg,
        )
