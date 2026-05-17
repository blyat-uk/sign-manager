"""Source of truth for label state. Wraps LabelState; emits typed signals."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from sub_label_pos.model.ass_file import AssFile
from sub_label_pos.model.label_state import LabelState
from sub_label_pos.model.mutations import Mutation
from sub_label_pos.model.types import LabelId
from sub_label_pos.model.undo_stack import UndoStack


class LabelStore(QObject):
    """Authoritative store for the current file's label state.

    Emits typed signals when state changes. Views subscribe and refresh from
    store state — they never mutate it directly.
    """

    labels_mutated = pyqtSignal(set)    # set[LabelId] — any property changed
    labels_added = pyqtSignal(set)      # set[LabelId] — new labels appeared
    labels_removed = pyqtSignal(set)    # set[LabelId] — labels deleted
    selection_changed = pyqtSignal(set) # set[LabelId]
    file_loaded = pyqtSignal(object)    # Path; using object so PyQt doesn't choke on Path
    dirty_changed = pyqtSignal(bool)

    def __init__(self) -> None:
        super().__init__()
        self._state = LabelState()
        self._selected: set[LabelId] = set()
        self._dirty = False
        self._source_path: Path | None = None
        self._undo = UndoStack()

    # --- Read-only views ------------------------------------------------

    @property
    def state(self) -> LabelState:
        """Direct (non-copied) handle to the live LabelState.

        Returned reference is mutable. Mutations applied to it directly will
        NOT emit signals; that contract is reserved for ``apply(mutation)``
        (Task E2). Read-only consumers should treat this as immutable.
        """
        return self._state

    @property
    def selected(self) -> set[LabelId]:
        return set(self._selected)

    @property
    def dirty(self) -> bool:
        return self._dirty

    @property
    def source_path(self) -> Path | None:
        return self._source_path

    @property
    def undo_stack(self) -> UndoStack:
        return self._undo

    # --- Mutations of meta state ---------------------------------------

    def load(self, ass: AssFile, source_path: Path) -> None:
        """Replace the entire state with the contents of an AssFile.

        All internal state is updated FIRST, then signals fire in the order:
        file_loaded -> selection_changed (if cleared) -> dirty_changed (if cleared).
        This guarantees subscribers see a fully consistent store at every signal.
        """
        # 1. State mutations (no signals yet)
        self._undo.clear()
        self._state = LabelState(
            labels={lbl.label_id: lbl for lbl in ass.labels},
            order=[lbl.label_id for lbl in ass.labels],
            styles=ass.styles_by_name(),
        )
        had_selection = bool(self._selected)
        was_dirty = self._dirty
        self._selected = set()
        self._dirty = False
        self._source_path = source_path

        # 2. Signal emissions in dependency order
        self.file_loaded.emit(source_path)
        if had_selection:
            self.selection_changed.emit(set())
        if was_dirty:
            self.dirty_changed.emit(False)

    def set_selection(self, sel: set[LabelId]) -> None:
        if sel == self._selected:
            return
        self._selected = set(sel)
        self.selection_changed.emit(set(self._selected))

    def mark_clean(self) -> None:
        if self._dirty:
            self._dirty = False
            self.dirty_changed.emit(False)

    def apply(self, mutation: Mutation) -> None:
        """Apply a mutation; capture inverse; emit kind-aware signals."""
        inverse = mutation.invert(self._state)   # snapshot inverse BEFORE applying
        affected_total = mutation.apply(self._state)
        self._undo.push(mutation, inverse)
        added, removed, mutated = self._classify(mutation, affected_total)
        if added:
            self.labels_added.emit(added)
        if removed:
            self.labels_removed.emit(removed)
        if mutated:
            self.labels_mutated.emit(mutated)
        # Drop any selected ids that no longer correspond to live labels.
        # Emits selection_changed AFTER the structural signals so subscribers
        # see a consistent store (removed labels are already gone before they
        # see the selection update).
        self._prune_dangling_selection()
        if not self._dirty:
            self._dirty = True
            self.dirty_changed.emit(True)

    def undo(self) -> None:
        """Undo the most recent mutation. No-op if undo stack empty."""
        if not self._undo.can_undo:
            return
        inv = self._undo.undo()
        affected = inv.apply(self._state)
        added, removed, mutated = self._classify(inv, affected)
        if added:
            self.labels_added.emit(added)
        if removed:
            self.labels_removed.emit(removed)
        if mutated:
            self.labels_mutated.emit(mutated)
        self._prune_dangling_selection()
        # Note: we don't toggle dirty here. The undo stack tracks history; if the
        # user undoes past the saved state, dirty stays True. (mark_clean is called
        # externally after a successful save.)

    def redo(self) -> None:
        """Redo the most recently undone mutation. No-op if redo stack empty."""
        if not self._undo.can_redo:
            return
        fwd = self._undo.redo()
        affected = fwd.apply(self._state)
        added, removed, mutated = self._classify(fwd, affected)
        if added:
            self.labels_added.emit(added)
        if removed:
            self.labels_removed.emit(removed)
        if mutated:
            self.labels_mutated.emit(mutated)
        self._prune_dangling_selection()

    def _prune_dangling_selection(self) -> None:
        """Drop selected ids that no longer correspond to live labels.

        Emits ``selection_changed`` if anything was removed. Used after
        structural mutations / undo / redo so the selection set never
        contains stale ids that subscribers would fail to resolve.
        """
        live = self._state.labels.keys()
        dangling = self._selected - live
        if dangling:
            self._selected -= dangling
            self.selection_changed.emit(set(self._selected))

    def _classify(self, mutation: Mutation, affected: set) -> tuple[set, set, set]:
        """Return (added_ids, removed_ids, mutated_ids) for signal dispatch.

        Inspects the mutation type structurally:
          - DeleteLabel -> removed
          - InsertLabel -> added
          - BatchMutation -> recurse over inner; any remaining ``affected`` ids
            not covered by inner classification are treated as ``mutated``.
          - Anything else -> mutated (default).
        Future structural mutations (Duplicate, Merge, Split) will be added here.
        """
        from sub_label_pos.model.mutations import (
            BatchMutation, DeleteLabel, InsertLabel, DuplicateLabel,
            MergeLabels, SplitMerged,
        )
        if isinstance(mutation, BatchMutation):
            added: set = set()
            removed: set = set()
            mutated: set = set()
            covered: set = set()
            for inner in mutation.inner:
                ia, ir, im = self._classify(inner, set())   # inner doesn't see top-level affected
                added |= ia
                removed |= ir
                mutated |= im
                covered |= ia | ir | im
            # Any ids in the top-level `affected` not covered are pure mutations
            mutated |= (affected - covered)
            # If an id appears in both added and removed (rare; del+insert in one batch),
            # treat it as mutated so subscribers don't see both signals for the same id.
            both = added & removed
            if both:
                added -= both
                removed -= both
                mutated |= both
            return added, removed, mutated
        if isinstance(mutation, DeleteLabel):
            return (set(), {mutation.label_id}, set())
        if isinstance(mutation, InsertLabel):
            return ({mutation.snapshot.label_id}, set(), set())
        if isinstance(mutation, DuplicateLabel):
            return ({mutation.new_label_id}, set(), set())
        if isinstance(mutation, MergeLabels):
            return (
                {mutation.merged_id},
                {s.label_id for s in mutation.source_snapshots},
                set(),
            )
        if isinstance(mutation, SplitMerged):
            return (
                {s.label_id for s in mutation.source_snapshots},
                {mutation.merged_id},
                set(),
            )
        return (set(), set(), set(affected))
