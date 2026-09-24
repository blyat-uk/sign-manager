"""Tests for LabelStore signals and basic operations."""

from pathlib import Path
import pytest

from sign_manager.model.ass_file import AssFile
from sign_manager.model.label_store import LabelStore

FIXTURE = Path(__file__).parent.parent / "fixtures" / "sample.ass"


def test_initial_state(qapp):
    store = LabelStore()
    assert store.state.labels == {}
    assert store.selected == set()
    assert store.dirty is False
    assert store.source_path is None


def test_load_populates_state(qapp):
    store = LabelStore()
    ass = AssFile.from_path(FIXTURE)
    store.load(ass, source_path=FIXTURE)
    assert len(store.state.labels) == len(ass.labels)
    assert store.state.order == [l.label_id for l in ass.labels]
    assert store.source_path == FIXTURE


def test_load_emits_file_loaded(qapp):
    store = LabelStore()
    received = []
    store.file_loaded.connect(lambda p: received.append(p))
    store.load(AssFile.from_path(FIXTURE), source_path=FIXTURE)
    assert received == [FIXTURE]


def test_load_resets_selection_and_dirty(qapp):
    store = LabelStore()
    store.load(AssFile.from_path(FIXTURE), source_path=FIXTURE)
    # Pre-load: mark as dirty by simulating an internal change (we lack mutations yet).
    store._dirty = True   # noqa: SLF001 (test-only)
    store._selected = {store.state.order[0]}
    received_dirty = []
    store.dirty_changed.connect(lambda b: received_dirty.append(b))
    store.load(AssFile.from_path(FIXTURE), source_path=FIXTURE)
    assert store.dirty is False
    assert store.selected == set()
    assert received_dirty == [False]


def test_set_selection_emits_when_changed(qapp):
    store = LabelStore()
    store.load(AssFile.from_path(FIXTURE), source_path=FIXTURE)
    lid = store.state.order[0]
    received = []
    store.selection_changed.connect(lambda s: received.append(s))
    store.set_selection({lid})
    assert received == [{lid}]
    assert store.selected == {lid}


def test_set_selection_no_emit_when_unchanged(qapp):
    store = LabelStore()
    store.load(AssFile.from_path(FIXTURE), source_path=FIXTURE)
    lid = store.state.order[0]
    store.set_selection({lid})
    received = []
    store.selection_changed.connect(lambda s: received.append(s))
    store.set_selection({lid})
    assert received == []


def test_selected_returns_a_copy(qapp):
    """Mutating the returned selection set MUST NOT change store state."""
    store = LabelStore()
    store.load(AssFile.from_path(FIXTURE), source_path=FIXTURE)
    lid = store.state.order[0]
    store.set_selection({lid})
    sel = store.selected
    sel.clear()  # mutate the copy
    assert store.selected == {lid}


def test_mark_clean_emits_when_dirty(qapp):
    store = LabelStore()
    store._dirty = True
    received = []
    store.dirty_changed.connect(lambda b: received.append(b))
    store.mark_clean()
    assert store.dirty is False
    assert received == [False]


def test_mark_clean_no_emit_when_already_clean(qapp):
    store = LabelStore()
    received = []
    store.dirty_changed.connect(lambda b: received.append(b))
    store.mark_clean()
    assert received == []


from sign_manager.model.mutations import BatchMutation
from sign_manager.model.types import LabelId


def test_apply_emits_labels_mutated(qapp):
    store = LabelStore()
    store.load(AssFile.from_path(FIXTURE), source_path=FIXTURE)
    lid = store.state.order[0]

    class _M:
        coalesce_key = None
        def apply(self, state):
            return {lid}
        def invert(self, state_before):
            return _M()

    received = []
    store.labels_mutated.connect(lambda s: received.append(s))
    store.apply(_M())
    assert received == [{lid}]


def test_apply_marks_dirty_and_emits_dirty_changed(qapp):
    store = LabelStore()
    store.load(AssFile.from_path(FIXTURE), source_path=FIXTURE)
    received = []
    store.dirty_changed.connect(lambda b: received.append(b))

    class _M:
        coalesce_key = None
        def apply(self, state):
            return set()
        def invert(self, state_before):
            return _M()

    store.apply(_M())
    assert store.dirty is True
    assert received == [True]


def test_apply_already_dirty_no_second_emit(qapp):
    store = LabelStore()
    store.load(AssFile.from_path(FIXTURE), source_path=FIXTURE)

    class _M:
        coalesce_key = None
        def apply(self, state): return set()
        def invert(self, state_before): return _M()

    store.apply(_M())   # first apply marks dirty
    received = []
    store.dirty_changed.connect(lambda b: received.append(b))
    store.apply(_M())   # second apply should NOT re-emit dirty_changed
    assert received == []


def test_apply_delete_emits_labels_removed(qapp):
    from sign_manager.model.mutations import DeleteLabel
    store = LabelStore()
    store.load(AssFile.from_path(FIXTURE), source_path=FIXTURE)
    lid = store.state.order[0]
    received_removed = []
    received_mutated = []
    store.labels_removed.connect(lambda s: received_removed.append(s))
    store.labels_mutated.connect(lambda s: received_mutated.append(s))
    store.apply(DeleteLabel.from_state(store.state, lid))
    assert received_removed == [{lid}]
    assert received_mutated == []   # not a mutate signal


def test_apply_delete_prunes_dangling_selection(qapp):
    """Deleting a selected label must drop it from store.selected and
    fire selection_changed so subscribers stay consistent."""
    from sign_manager.model.mutations import DeleteLabel
    store = LabelStore()
    store.load(AssFile.from_path(FIXTURE), source_path=FIXTURE)
    lid_a, lid_b = store.state.order[0], store.state.order[1]
    store.set_selection({lid_a, lid_b})
    received_selection = []
    store.selection_changed.connect(lambda s: received_selection.append(s))
    store.apply(DeleteLabel.from_state(store.state, lid_a))
    assert store.selected == {lid_b}
    assert received_selection == [{lid_b}]


def test_apply_delete_unselected_no_selection_emit(qapp):
    """Deleting a label that isn't selected must NOT fire selection_changed."""
    from sign_manager.model.mutations import DeleteLabel
    store = LabelStore()
    store.load(AssFile.from_path(FIXTURE), source_path=FIXTURE)
    lid_a, lid_b = store.state.order[0], store.state.order[1]
    store.set_selection({lid_a})
    received_selection = []
    store.selection_changed.connect(lambda s: received_selection.append(s))
    store.apply(DeleteLabel.from_state(store.state, lid_b))
    assert store.selected == {lid_a}
    assert received_selection == []


def test_undo_of_insert_prunes_dangling_selection(qapp):
    """Undoing an insert (which removes the label) must clean dangling selection."""
    from sign_manager.model.mutations import DeleteLabel, InsertLabel
    store = LabelStore()
    store.load(AssFile.from_path(FIXTURE), source_path=FIXTURE)
    lid = store.state.order[0]
    deleted = DeleteLabel.from_state(store.state, lid)
    store.apply(deleted)
    store.apply(InsertLabel(snapshot=deleted.snapshot))
    store.set_selection({lid})
    received_selection = []
    store.selection_changed.connect(lambda s: received_selection.append(s))
    store.undo()  # undoes the insert -> removes lid
    assert store.selected == set()
    assert received_selection == [set()]


def test_apply_insert_emits_labels_added(qapp):
    from sign_manager.model.mutations import DeleteLabel, InsertLabel
    store = LabelStore()
    store.load(AssFile.from_path(FIXTURE), source_path=FIXTURE)
    lid = store.state.order[0]
    deleted = DeleteLabel.from_state(store.state, lid)
    store.apply(deleted)
    received_added = []
    store.labels_added.connect(lambda s: received_added.append(s))
    store.apply(InsertLabel(snapshot=deleted.snapshot))
    assert received_added == [{lid}]


def test_apply_move_still_emits_labels_mutated(qapp):
    from sign_manager.model.mutations import MoveLabel
    store = LabelStore()
    store.load(AssFile.from_path(FIXTURE), source_path=FIXTURE)
    lid = store.state.order[0]
    received = []
    store.labels_mutated.connect(lambda s: received.append(s))
    store.apply(MoveLabel(label_id=lid, new_x=1.0, new_y=2.0))
    assert received == [{lid}]


def test_apply_batch_dispatches_per_inner_mutation(qapp):
    from sign_manager.model.mutations import BatchMutation, MoveLabel, DeleteLabel
    store = LabelStore()
    store.load(AssFile.from_path(FIXTURE), source_path=FIXTURE)
    lid_move = store.state.order[0]
    lid_del = store.state.order[1]
    received_mutated = []
    received_removed = []
    store.labels_mutated.connect(lambda s: received_mutated.append(s))
    store.labels_removed.connect(lambda s: received_removed.append(s))
    batch = BatchMutation([
        MoveLabel(label_id=lid_move, new_x=1.0, new_y=2.0),
        DeleteLabel.from_state(store.state, lid_del),
    ])
    store.apply(batch)
    assert received_mutated == [{lid_move}]
    assert received_removed == [{lid_del}]


def test_apply_batch_with_del_and_insert_same_id_emits_mutated_only(qapp):
    """When a batch deletes and re-inserts the same id, classifier
    collapses both to labels_mutated (avoid double-signal per id)."""
    from sign_manager.model.mutations import BatchMutation, DeleteLabel, InsertLabel
    store = LabelStore()
    store.load(AssFile.from_path(FIXTURE), source_path=FIXTURE)
    lid = store.state.order[0]
    deleted = DeleteLabel.from_state(store.state, lid)
    received_added = []
    received_removed = []
    received_mutated = []
    store.labels_added.connect(lambda s: received_added.append(s))
    store.labels_removed.connect(lambda s: received_removed.append(s))
    store.labels_mutated.connect(lambda s: received_mutated.append(s))
    store.apply(BatchMutation([deleted, InsertLabel(snapshot=deleted.snapshot)]))
    # Collision rule: same id in added and removed -> collapse to mutated
    assert received_added == []
    assert received_removed == []
    assert received_mutated == [{lid}]
    # State should still have the label (delete then reinsert is net-zero structurally)
    assert lid in store.state.labels


def test_apply_duplicate_emits_labels_added(qapp):
    from sign_manager.model.mutations import DuplicateLabel
    from sign_manager.model.types import LabelId
    store = LabelStore()
    store.load(AssFile.from_path(FIXTURE), source_path=FIXTURE)
    src = store.state.order[0]
    new_id = LabelId("dup-1")
    received_added = []
    received_mutated = []
    store.labels_added.connect(lambda s: received_added.append(s))
    store.labels_mutated.connect(lambda s: received_mutated.append(s))
    store.apply(DuplicateLabel(
        source_label_id=src, new_label_id=new_id, position_offset=(10.0, 5.0),
    ))
    assert received_added == [{new_id}]
    assert received_mutated == []


def test_apply_merge_emits_added_for_merged_removed_for_sources(qapp):
    from sign_manager.model.mutations import MergeLabels
    from sign_manager.model.types import LabelId, LabelSnapshot
    from dataclasses import replace as dc_replace
    store = LabelStore()
    store.load(AssFile.from_path(FIXTURE), source_path=FIXTURE)
    src_ids = list(store.state.order[:2])
    snaps = tuple(
        LabelSnapshot(label_id=l, dialogue=store.state.labels[l], line_index=store.state.index_of(l))
        for l in src_ids
    )
    merged_id = LabelId("merged-1")
    merged_dlg = dc_replace(snaps[0].dialogue, label_id=merged_id)
    received_added = []
    received_removed = []
    received_mutated = []
    store.labels_added.connect(lambda s: received_added.append(s))
    store.labels_removed.connect(lambda s: received_removed.append(s))
    store.labels_mutated.connect(lambda s: received_mutated.append(s))
    store.apply(MergeLabels(
        source_snapshots=snaps, merged_id=merged_id, merged_dialogue=merged_dlg,
    ))
    assert received_added == [{merged_id}]
    assert received_removed == [set(src_ids)]
    assert received_mutated == []


def test_apply_split_merged_emits_added_for_sources_removed_for_merged(qapp):
    from sign_manager.model.mutations import MergeLabels, SplitMerged
    from sign_manager.model.types import LabelId, LabelSnapshot
    from dataclasses import replace as dc_replace
    store = LabelStore()
    store.load(AssFile.from_path(FIXTURE), source_path=FIXTURE)
    src_ids = list(store.state.order[:2])
    snaps = tuple(
        LabelSnapshot(label_id=l, dialogue=store.state.labels[l], line_index=store.state.index_of(l))
        for l in src_ids
    )
    merged_id = LabelId("merged-1")
    merged_dlg = dc_replace(snaps[0].dialogue, label_id=merged_id)
    store.apply(MergeLabels(
        source_snapshots=snaps, merged_id=merged_id, merged_dialogue=merged_dlg,
    ))
    received_added = []
    received_removed = []
    store.labels_added.connect(lambda s: received_added.append(s))
    store.labels_removed.connect(lambda s: received_removed.append(s))
    store.apply(SplitMerged(source_snapshots=snaps, merged_id=merged_id))
    assert received_added == [set(src_ids)]
    assert received_removed == [{merged_id}]


def test_apply_records_inverse_on_undo_stack(qapp):
    from sign_manager.model.mutations import MoveLabel
    store = LabelStore()
    store.load(AssFile.from_path(FIXTURE), source_path=FIXTURE)
    assert not store.undo_stack.can_undo
    lid = store.state.order[0]
    store.apply(MoveLabel(label_id=lid, new_x=999.0, new_y=42.0))
    assert store.undo_stack.can_undo
    assert not store.undo_stack.can_redo


def test_undo_restores_state(qapp):
    from sign_manager.model.mutations import MoveLabel
    store = LabelStore()
    store.load(AssFile.from_path(FIXTURE), source_path=FIXTURE)
    lid = store.state.order[0]
    before = (store.state.labels[lid].pos_x, store.state.labels[lid].pos_y)
    store.apply(MoveLabel(label_id=lid, new_x=999.0, new_y=42.0))
    store.undo()
    assert (store.state.labels[lid].pos_x, store.state.labels[lid].pos_y) == before


def test_redo_reapplies(qapp):
    from sign_manager.model.mutations import MoveLabel
    store = LabelStore()
    store.load(AssFile.from_path(FIXTURE), source_path=FIXTURE)
    lid = store.state.order[0]
    store.apply(MoveLabel(label_id=lid, new_x=999.0, new_y=42.0))
    store.undo()
    store.redo()
    assert store.state.labels[lid].pos_x == 999
    assert store.state.labels[lid].pos_y == 42


def test_undo_emits_kind_aware_signals(qapp):
    """Undoing a DeleteLabel should emit labels_added (reverse of the original)."""
    from sign_manager.model.mutations import DeleteLabel
    store = LabelStore()
    store.load(AssFile.from_path(FIXTURE), source_path=FIXTURE)
    lid = store.state.order[0]
    store.apply(DeleteLabel.from_state(store.state, lid))
    received_added = []
    received_removed = []
    store.labels_added.connect(lambda s: received_added.append(s))
    store.labels_removed.connect(lambda s: received_removed.append(s))
    store.undo()   # undoes the delete -> should add lid back
    assert received_added == [{lid}]
    assert received_removed == []
    assert lid in store.state.labels


def test_load_clears_undo_stack(qapp):
    from sign_manager.model.mutations import MoveLabel
    store = LabelStore()
    store.load(AssFile.from_path(FIXTURE), source_path=FIXTURE)
    lid = store.state.order[0]
    store.apply(MoveLabel(label_id=lid, new_x=1.0, new_y=2.0))
    assert store.undo_stack.can_undo
    store.load(AssFile.from_path(FIXTURE), source_path=FIXTURE)
    assert not store.undo_stack.can_undo
    assert not store.undo_stack.can_redo


def test_undo_when_empty_is_noop(qapp):
    """undo() with empty stack should not raise; should not change state."""
    store = LabelStore()
    store.load(AssFile.from_path(FIXTURE), source_path=FIXTURE)
    # Should not raise
    store.undo()
    # State unchanged, stacks empty
    assert not store.undo_stack.can_undo
    assert not store.undo_stack.can_redo


def test_redo_when_empty_is_noop(qapp):
    store = LabelStore()
    store.load(AssFile.from_path(FIXTURE), source_path=FIXTURE)
    store.redo()
    assert not store.undo_stack.can_redo
