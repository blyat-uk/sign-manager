"""Tests for LabelEditController -- UI intents to mutations.

L1: the controller no longer mirrors edits onto a parallel ``AssFile``;
all edits route through ``LabelStore.apply``. These tests verify the
store-only behaviour. Save-time AssFile reconstruction is covered by
``tests/ui/test_save_flow.py`` and ``tests/model/test_ass_file.py``.
"""

from pathlib import Path

import pytest

from sub_label_pos.model.ass_file import AssFile
from sub_label_pos.model.label_store import LabelStore
from sub_label_pos.model.types import StylePatch
from sub_label_pos.ui.controllers.label_edit_controller import LabelEditController

FIXTURE = Path(__file__).parent.parent / "fixtures" / "sample.ass"


def _make_controller(qapp):
    """Build a (controller, store) pair from the sample fixture."""
    ass = AssFile(str(FIXTURE))
    store = LabelStore()
    store.load(ass, FIXTURE)
    ctrl = LabelEditController(store)
    return ctrl, store


def test_move_applies_mutation(qapp):
    ctrl, store = _make_controller(qapp)
    lid = store.state.order[0]
    ctrl.move(lid, 999, 42)
    assert store.state.labels[lid].pos_x == 999
    assert store.state.labels[lid].pos_y == 42
    assert store.undo_stack.can_undo


def test_move_many_is_a_batch(qapp):
    ctrl, store = _make_controller(qapp)
    lids = list(store.state.order[:2])
    before = {lid: (store.state.labels[lid].pos_x, store.state.labels[lid].pos_y)
              for lid in lids}
    ctrl.move_many({lids[0]: (100.0, 200.0), lids[1]: (300.0, 400.0)})
    assert store.state.labels[lids[0]].pos_x == 100
    assert store.state.labels[lids[1]].pos_x == 300
    # Single batch -> single undo entry restores both
    ctrl.undo()
    for lid, (x, y) in before.items():
        assert store.state.labels[lid].pos_x == x
        assert store.state.labels[lid].pos_y == y


def test_resize_applies_to_store(qapp):
    ctrl, store = _make_controller(qapp)
    lid = store.state.order[0]
    ctrl.resize(lid, 99)
    assert store.state.labels[lid].font_size == 99


def test_delete_single(qapp):
    ctrl, store = _make_controller(qapp)
    lid = store.state.order[0]
    ctrl.delete({lid})
    assert lid not in store.state.labels


def test_delete_many_is_a_batch(qapp):
    ctrl, store = _make_controller(qapp)
    initial_count = len(store.state.order)
    if initial_count < 2:
        pytest.skip("Fixture lacks 2 labels")
    ids = set(store.state.order[:2])
    ctrl.delete(ids)
    assert len(store.state.order) == initial_count - 2
    # Single undo restores both
    ctrl.undo()
    assert len(store.state.order) == initial_count


def test_change_style_many_applies_patch(qapp):
    ctrl, store = _make_controller(qapp)
    if len(store.state.order) < 2:
        pytest.skip("Fixture lacks 2 labels")
    ids = set(store.state.order[:2])
    ctrl.change_style_many(ids, StylePatch(bold=True))
    for lid in ids:
        assert store.state.labels[lid].bold is True


def test_copy_paste_style(qapp):
    ctrl, store = _make_controller(qapp)
    if len(store.state.order) < 2:
        pytest.skip("Fixture lacks 2 labels")
    src = store.state.order[0]
    target = store.state.order[1]
    # Modify source style first
    ctrl.change_style(src, StylePatch(bold=True, font_size=99))
    ctrl.copy_style_from(src)
    assert ctrl.has_pasted_style
    ctrl.paste_style_to({target})
    assert store.state.labels[target].bold is True
    assert store.state.labels[target].font_size == 99


def test_duplicate_returns_new_id(qapp):
    ctrl, store = _make_controller(qapp)
    src = store.state.order[0]
    new_id = ctrl.duplicate(src)
    assert new_id != src
    assert new_id in store.state.labels


def test_create_label(qapp):
    ctrl, store = _make_controller(qapp)
    before = len(store.state.order)
    new_id = ctrl.create_label(
        pos_x=100, pos_y=200, start_time=0.0, end_time=1.0, text="Hello",
    )
    assert new_id is not None
    assert len(store.state.order) == before + 1
    assert new_id in store.state.labels
    new_dlg = store.state.labels[new_id]
    assert new_dlg.pos_x == 100
    assert new_dlg.pos_y == 200
    assert new_dlg.text == "Hello"


def test_retime_updates_times(qapp):
    ctrl, store = _make_controller(qapp)
    lid = store.state.order[0]
    ctrl.retime(lid, 5.0, 10.0)
    assert store.state.labels[lid].start_time == 5.0
    assert store.state.labels[lid].end_time == 10.0


def test_undo_redo_round_trip(qapp):
    ctrl, store = _make_controller(qapp)
    lid = store.state.order[0]
    original_x = store.state.labels[lid].pos_x
    ctrl.move(lid, 555, 666)
    ctrl.undo()
    assert store.state.labels[lid].pos_x == original_x
    ctrl.redo()
    assert store.state.labels[lid].pos_x == 555


def test_merge_creates_combined_label(qapp):
    ctrl, store = _make_controller(qapp)
    if len(store.state.order) < 2:
        pytest.skip("Fixture lacks 2 labels")
    ids = list(store.state.order[:2])
    text_a = store.state.labels[ids[0]].text
    text_b = store.state.labels[ids[1]].text
    merged_id = ctrl.merge(ids, order=[0, 1], separator=" ")
    assert merged_id is not None
    assert merged_id in store.state.labels
    assert ids[0] not in store.state.labels
    assert ids[1] not in store.state.labels
    merged = store.state.labels[merged_id]
    assert merged.text == f"{text_a} {text_b}"


def test_retime_each_submits_batch_with_per_label_times(qapp):
    ctrl, store = _make_controller(qapp)
    lids = list(store.state.order)[:2]
    ctrl.retime_each([
        (lids[0], 10.0, 12.0),
        (lids[1], 20.0, 22.0),
    ])
    assert store.state.labels[lids[0]].start_time == 10.0
    assert store.state.labels[lids[0]].end_time == 12.0
    assert store.state.labels[lids[1]].start_time == 20.0
    assert store.state.labels[lids[1]].end_time == 22.0


def test_retime_each_with_single_update_still_works(qapp):
    ctrl, store = _make_controller(qapp)
    lid = store.state.order[0]
    ctrl.retime_each([(lid, 7.5, 9.5)])
    assert store.state.labels[lid].start_time == 7.5
    assert store.state.labels[lid].end_time == 9.5


def test_retime_each_empty_list_is_noop(qapp):
    ctrl, store = _make_controller(qapp)
    before = {lid: (lb.start_time, lb.end_time) for lid, lb in store.state.labels.items()}
    ctrl.retime_each([])
    after = {lid: (lb.start_time, lb.end_time) for lid, lb in store.state.labels.items()}
    assert before == after


def test_retime_each_passes_coalesce_key_through_to_batch(qapp):
    ctrl, store = _make_controller(qapp)
    lids = list(store.state.order)[:2]
    before = {lid: (store.state.labels[lid].start_time, store.state.labels[lid].end_time)
              for lid in lids}
    # First call
    ctrl.retime_each([(lids[0], 1.0, 2.0), (lids[1], 1.0, 2.0)], coalesce_key="drag:sess1")
    # Second call within 500ms with same key should coalesce in the undo stack
    ctrl.retime_each([(lids[0], 3.0, 4.0), (lids[1], 3.0, 4.0)], coalesce_key="drag:sess1")
    # One undo should restore both labels to their pre-first-call state.
    store.undo()
    for lid in lids:
        assert (store.state.labels[lid].start_time, store.state.labels[lid].end_time) == before[lid]
