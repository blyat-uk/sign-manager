"""LabelEditController — translates UI intents into Mutation submissions.

All edits route through :py:meth:`LabelStore.apply` (and therefore through a
``Mutation``). The store is the single source of truth; the on-disk AssFile
is reconstructed from ``state`` at save time (see
:py:meth:`AssFile.from_state`), so the controller no longer mirrors edits to
a parallel ``AssFile`` instance -- a change that was previously required to
keep ``ass.lines`` current for the legacy save path.
"""

from __future__ import annotations

from dataclasses import replace

from PyQt6.QtCore import QObject

from sub_label_pos.model.ass_file import LabelDialogue
from sub_label_pos.model.label_store import LabelStore
from sub_label_pos.model.mutations import (
    BatchMutation,
    ChangeStyle,
    DeleteLabel,
    DuplicateLabel,
    EditText,
    InsertLabel,
    MergeLabels,
    MoveLabel,
    Mutation,
    PasteStyle,
    ResizeLabel,
    RetimeLabel,
    RotateLabel,
)
from sub_label_pos.model.types import (
    LabelId,
    LabelSnapshot,
    StylePatch,
    new_label_id,
)


class LabelEditController(QObject):
    """UI-facing edit interface backed by LabelStore.

    Widgets call high-level methods (move, resize, change_style, ...) and the
    controller constructs the appropriate Mutation and submits it via
    ``store.apply()``. Multi-target operations are wrapped in BatchMutation so
    they're a single undo step.
    """

    def __init__(self, store: LabelStore) -> None:
        super().__init__()
        self._store = store
        self._pasted_style: StylePatch | None = None

    # --- Public read-only views -----------------------------------

    @property
    def store(self) -> LabelStore:
        return self._store

    @property
    def has_pasted_style(self) -> bool:
        return self._pasted_style is not None

    # --- Generic submit + undo/redo --------------------------------

    def submit(self, mutation: Mutation) -> None:
        self._store.apply(mutation)

    def undo(self) -> None:
        self._store.undo()

    def redo(self) -> None:
        self._store.redo()

    # --- Movement / sizing -----------------------------------------

    def move(self, label_id: LabelId, new_x: float, new_y: float) -> None:
        self.submit(MoveLabel(label_id=label_id, new_x=new_x, new_y=new_y))

    def move_many(self, deltas: dict[LabelId, tuple[float, float]]) -> None:
        """deltas: ``dict[LabelId, (new_x, new_y)]``. Submits as one batch."""
        if not deltas:
            return
        mutations = [
            MoveLabel(label_id=lid, new_x=xy[0], new_y=xy[1])
            for lid, xy in deltas.items()
        ]
        if len(mutations) == 1:
            self.submit(mutations[0])
        else:
            self.submit(BatchMutation(mutations))

    def resize(self, label_id: LabelId, new_font_size: int) -> None:
        self.submit(ResizeLabel(label_id=label_id, new_font_size=new_font_size))

    def rotate(self, label_id: LabelId, new_angle: float) -> None:
        self.submit(RotateLabel(label_id=label_id, new_angle=new_angle))

    # --- Text / style ----------------------------------------------

    def edit_text(self, label_id: LabelId, new_text: str, new_rich_text: str) -> None:
        self.submit(EditText(
            label_id=label_id, new_text=new_text, new_rich_text=new_rich_text,
        ))

    def change_style(self, label_id: LabelId, patch: StylePatch) -> None:
        self.submit(ChangeStyle(label_id=label_id, patch=patch))

    def change_style_many(self, label_ids: set[LabelId], patch: StylePatch) -> None:
        if not label_ids:
            return
        if len(label_ids) == 1:
            lid = next(iter(label_ids))
            self.submit(ChangeStyle(label_id=lid, patch=patch))
            return
        self.submit(BatchMutation([
            ChangeStyle(label_id=lid, patch=patch) for lid in label_ids
        ]))

    def change_style_name(self, label_id: LabelId, style_name: str) -> None:
        """Change a label's named style assignment.

        ``style_name`` is not part of ``StylePatch`` (which models per-label
        inline overrides), so there is no dedicated Mutation for it yet.
        This applies the change directly on the live state (clearing the
        inline ``font_size`` override so the new style's size takes effect)
        and re-emits ``labels_mutated`` so derived models and the toolbar
        refresh. Save serializes from state so the change persists.
        """
        state = self._store.state
        if style_name not in state.styles:
            return
        dlg = state.labels.get(label_id)
        if dlg is None:
            return
        state.labels[label_id] = replace(
            dlg, style_name=style_name, font_size=None,
        )
        self._store.labels_mutated.emit({label_id})

    # --- Time --------------------------------------------------------

    def retime(self, label_id: LabelId, new_start: float, new_end: float) -> None:
        self.submit(RetimeLabel(
            label_id=label_id, new_start=new_start, new_end=new_end,
        ))

    def retime_many(
        self, label_ids: list[LabelId], new_start: float, new_end: float,
    ) -> None:
        if not label_ids:
            return
        mutations = [
            RetimeLabel(label_id=lid, new_start=new_start, new_end=new_end)
            for lid in label_ids
        ]
        if len(mutations) == 1:
            self.submit(mutations[0])
        else:
            self.submit(BatchMutation(mutations))

    def retime_each(
        self,
        updates: list[tuple[LabelId, float, float]],
        *,
        coalesce_key: str | None = None,
    ) -> None:
        """Apply per-label (start, end) times in one batch.

        Each tuple is (label_id, new_start, new_end). Unlike ``retime_many``
        (which aligns every label to the same span), this method lets each
        label receive its own pair. Used by shift / nudge_both / drag flows.

        Pass ``coalesce_key`` to mark the batch as coalescing with consecutive
        batches sharing the same key (used by drag sessions for one-undo-entry-
        per-drag semantics).
        """
        if not updates:
            return
        mutations = [
            RetimeLabel(label_id=lid, new_start=ns, new_end=ne)
            for lid, ns, ne in updates
        ]
        self.submit(BatchMutation(mutations, coalesce_key=coalesce_key))

    # --- Structural -------------------------------------------------

    def duplicate(
        self,
        source_label_id: LabelId,
        position_offset: tuple[float, float] = (30.0, 30.0),
    ) -> LabelId:
        """Duplicate a label. Returns the new id."""
        new_id = new_label_id()
        self.submit(DuplicateLabel(
            source_label_id=source_label_id,
            new_label_id=new_id,
            position_offset=position_offset,
        ))
        return new_id

    def create_label(
        self,
        pos_x: int,
        pos_y: int,
        start_time: float,
        end_time: float,
        text: str = "New Label",
        font_size: int | None = None,
        style_name: str | None = None,
    ) -> LabelId | None:
        """Create a new label and submit an ``InsertLabel`` mutation.

        Synthesizes a fresh ``LabelDialogue`` from the supplied fields and
        inserts it at the end of the current order. ``style_name`` defaults
        to the first available style on the store; if no styles exist the
        label is created with style ``"Default"`` (it will round-trip via
        ``from_state`` regardless).
        """
        state = self._store.state
        if style_name is None:
            style_name = next(iter(state.styles), "Default")
        new_id = new_label_id()
        dlg = LabelDialogue(
            line_index=len(state.order),
            start_time=start_time,
            end_time=end_time,
            pos_x=int(pos_x),
            pos_y=int(pos_y),
            text=text,
            font_size=font_size,
            style_name=style_name,
            rich_text=text,
            label_id=new_id,
        )
        snap = LabelSnapshot(
            label_id=new_id,
            dialogue=dlg,
            line_index=len(state.order),
        )
        self.submit(InsertLabel(snapshot=snap))
        return new_id

    def delete(self, label_ids: set[LabelId]) -> None:
        if not label_ids:
            return
        # Snapshot deletions BEFORE applying (DeleteLabel.from_state requires it).
        if len(label_ids) == 1:
            lid = next(iter(label_ids))
            self.submit(DeleteLabel.from_state(self._store.state, lid))
        else:
            snapshots = [
                DeleteLabel.from_state(self._store.state, lid) for lid in label_ids
            ]
            self.submit(BatchMutation(snapshots))

    def merge(
        self,
        label_ids: list[LabelId],
        order: list[int],
        separator: str,
    ) -> LabelId | None:
        """Merge a list of labels into one.

        ``label_ids`` order matches the original ``labels`` arg used by the
        widget's merge submenu; ``order`` permutes the indices into that list
        to determine text join order; ``separator`` is the join string.
        """
        if not label_ids:
            return None
        state = self._store.state
        if any(lid not in state.labels for lid in label_ids):
            return None
        # Snapshot the source state BEFORE submitting.
        snapshots = tuple(
            LabelSnapshot(
                label_id=lid,
                dialogue=state.labels[lid],
                line_index=state.index_of(lid),
            )
            for lid in label_ids
        )
        # Compute merged dialogue: text from `order`, anchor on earliest
        # (by start_time then line_index) source for position / style / etc.
        source_dialogues = [state.labels[lid] for lid in label_ids]
        anchor = min(
            source_dialogues,
            key=lambda d: (d.start_time, state.index_of(d.label_id)),
        )
        merged_text = separator.join(source_dialogues[i].text for i in order)
        start_time = min(d.start_time for d in source_dialogues)
        end_time = max(d.end_time for d in source_dialogues)
        merged_id = new_label_id()
        merged_dialogue = replace(
            anchor,
            label_id=merged_id,
            text=merged_text,
            rich_text=merged_text,
            start_time=start_time,
            end_time=end_time,
        )
        self.submit(MergeLabels(
            source_snapshots=snapshots,
            merged_id=merged_id,
            merged_dialogue=merged_dialogue,
        ))
        return merged_id

    # --- Style copy/paste -----------------------------------------

    def copy_style_from(self, label_id: LabelId) -> None:
        """Snapshot the style fields of a label for later paste."""
        dlg = self._store.state.labels[label_id]
        self._pasted_style = StylePatch(
            font_size=dlg.font_size,
            primary_colour=dlg.primary_colour,
            outline_colour=dlg.outline_colour,
            outline_width=dlg.outline_width,
            bold=dlg.bold,
            italic=dlg.italic,
            alignment=dlg.alignment,
            rotation=dlg.rotation,
        )

    def paste_style_to(self, label_ids: set[LabelId]) -> None:
        if self._pasted_style is None or not label_ids:
            return
        patch = self._pasted_style
        if len(label_ids) == 1:
            lid = next(iter(label_ids))
            self.submit(PasteStyle(label_id=lid, patch=patch))
            return
        self.submit(BatchMutation([
            PasteStyle(label_id=lid, patch=patch) for lid in label_ids
        ]))

    def set_pasted_style(self, patch: StylePatch | None) -> None:
        """Set the pasted-style buffer directly (used by the legacy
        ``_on_copy_style`` flow that exposes a per-attribute selector)."""
        self._pasted_style = patch
