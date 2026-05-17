"""Tests for DerivedGroupModel — incremental groups derived from LabelStore."""

from pathlib import Path
from dataclasses import replace

import pytest

from sub_label_pos.model.ass_file import AssFile, LabelDialogue
from sub_label_pos.model.groups import DerivedGroupModel, LabelGroup
from sub_label_pos.model.label_store import LabelStore
from sub_label_pos.model.mutations import (
    MoveLabel, DeleteLabel, RetimeLabel, InsertLabel,
)
from sub_label_pos.model.types import LabelId, LabelSnapshot, new_label_id

FIXTURE = Path(__file__).parent.parent / "fixtures" / "sample.ass"


# --- helpers --------------------------------------------------------------

def _make_dialogue(
    start: float,
    end: float,
    text: str = "x",
    pos_x: int = 100,
    pos_y: int = 200,
) -> LabelDialogue:
    """Construct a minimal LabelDialogue with a fresh label_id."""
    return LabelDialogue(
        line_index=0,
        start_time=start,
        end_time=end,
        pos_x=pos_x,
        pos_y=pos_y,
        text=text,
        label_id=new_label_id(),
    )


def _insert_dialogue(store: LabelStore, dlg: LabelDialogue) -> LabelId:
    """Insert a fresh dialogue at the end of the store via InsertLabel."""
    snap = LabelSnapshot(
        label_id=dlg.label_id,
        dialogue=dlg,
        line_index=len(store.state.order),
    )
    store.apply(InsertLabel(snapshot=snap))
    return dlg.label_id


# --- existing tests -------------------------------------------------------

def test_groups_empty_before_load(qapp):
    store = LabelStore()
    groups = DerivedGroupModel(store)
    assert groups.groups == []


def test_groups_after_load_one_group_per_time_window(qapp):
    """The sample fixture has two labels with the same (start,end) — they form one group."""
    store = LabelStore()
    groups = DerivedGroupModel(store)
    store.load(AssFile.from_path(FIXTURE), source_path=FIXTURE)
    gs = groups.groups
    assert len(gs) == 1
    assert len(gs[0].label_ids) == 2


def test_groups_recomputed_on_mutation_that_changes_time(qapp):
    """Changing a label's time should split or merge groups."""
    store = LabelStore()
    groups = DerivedGroupModel(store)
    store.load(AssFile.from_path(FIXTURE), source_path=FIXTURE)
    assert len(groups.groups) == 1
    # Retime the first label to a different, non-overlapping window
    lid = store.state.order[0]
    store.apply(RetimeLabel(label_id=lid, new_start=99.0, new_end=100.0))
    gs = groups.groups
    assert len(gs) == 2   # now two non-overlapping intervals


def test_groups_unchanged_when_mutation_doesnt_affect_time(qapp):
    """Moving a label (position change) shouldn't affect groups."""
    store = LabelStore()
    groups = DerivedGroupModel(store)
    store.load(AssFile.from_path(FIXTURE), source_path=FIXTURE)
    before = groups.groups
    lid = store.state.order[0]
    store.apply(MoveLabel(label_id=lid, new_x=999.0, new_y=42.0))
    after = groups.groups
    # Same group structure (label_ids, time windows)
    assert len(after) == len(before)
    assert after[0].label_ids == before[0].label_ids


def test_groups_recompute_on_label_removed(qapp):
    """Deleting a label should remove it from its group (or remove the group)."""
    store = LabelStore()
    groups = DerivedGroupModel(store)
    store.load(AssFile.from_path(FIXTURE), source_path=FIXTURE)
    before = groups.groups
    lid = store.state.order[0]
    store.apply(DeleteLabel.from_state(store.state, lid))
    after = groups.groups
    # Group should still exist but with one fewer label
    assert len(after) == 1
    assert len(after[0].label_ids) == len(before[0].label_ids) - 1


def test_groups_changed_signal_fires_on_relevant_mutations(qapp):
    store = LabelStore()
    groups = DerivedGroupModel(store)
    received = []
    groups.groups_changed.connect(lambda _: received.append(True))
    store.load(AssFile.from_path(FIXTURE), source_path=FIXTURE)
    # Load should trigger a recompute and emit
    assert received, "groups_changed should fire on file_loaded"


def test_representative_time_is_a_best_fit_candidate(qapp):
    """For the fixture (two labels both at 1.0–3.0), the best-fit time equals
    the per-label midpoint (2.0), where both labels are visible."""
    store = LabelStore()
    groups = DerivedGroupModel(store)
    store.load(AssFile.from_path(FIXTURE), source_path=FIXTURE)
    g = groups.groups[0]
    # Both labels share the same window, so midpoint == best-fit time.
    assert g.representative_time == pytest.approx(2.0)
    # And both labels are visible at that time.
    visible = sum(
        1 for lid in g.label_ids
        if store.state.labels[lid].start_time
        <= g.representative_time
        <= store.state.labels[lid].end_time
    )
    assert visible == len(g.label_ids)


def test_groups_in_label_order_after_retime(qapp):
    """Groups should be ordered by their earliest start_time (interval-merge
    sorts by start_time)."""
    store = LabelStore()
    groups = DerivedGroupModel(store)
    store.load(AssFile.from_path(FIXTURE), source_path=FIXTURE)
    lid_a = store.state.order[0]  # was (1.0, 3.0)
    lid_b = store.state.order[1]  # still (1.0, 3.0)
    # Retime A to a much later, non-overlapping window
    store.apply(RetimeLabel(label_id=lid_a, new_start=99.0, new_end=100.0))
    gs = groups.groups
    assert len(gs) == 2
    # First group is the one with the earliest start_time (B at 1.0).
    assert gs[0].label_ids == (lid_b,)
    assert gs[1].label_ids == (lid_a,)


# --- new interval-overlap tests -------------------------------------------

def test_overlapping_windows_form_one_group(qapp):
    """Two labels with overlapping (but not identical) windows merge into
    a single group."""
    store = LabelStore()
    groups = DerivedGroupModel(store)
    a = _make_dialogue(1.0, 3.0, text="A")
    b = _make_dialogue(2.0, 4.0, text="B")
    _insert_dialogue(store, a)
    _insert_dialogue(store, b)
    gs = groups.groups
    assert len(gs) == 1
    assert set(gs[0].label_ids) == {a.label_id, b.label_id}
    assert gs[0].start == pytest.approx(1.0)
    assert gs[0].end == pytest.approx(4.0)


def test_non_overlapping_windows_form_two_groups(qapp):
    """Two labels with disjoint windows produce two separate groups."""
    store = LabelStore()
    groups = DerivedGroupModel(store)
    a = _make_dialogue(1.0, 3.0, text="A")
    b = _make_dialogue(5.0, 7.0, text="B")
    _insert_dialogue(store, a)
    _insert_dialogue(store, b)
    gs = groups.groups
    assert len(gs) == 2
    # Earlier start_time first
    assert gs[0].label_ids == (a.label_id,)
    assert gs[1].label_ids == (b.label_id,)


def test_touching_windows_form_one_group(qapp):
    """Windows that touch at a single instant (end_a == start_b) still merge,
    because the legacy rule is ``start <= max_end`` (inclusive)."""
    store = LabelStore()
    groups = DerivedGroupModel(store)
    a = _make_dialogue(1.0, 3.0, text="A")
    b = _make_dialogue(3.0, 5.0, text="B")
    _insert_dialogue(store, a)
    _insert_dialogue(store, b)
    gs = groups.groups
    assert len(gs) == 1
    assert set(gs[0].label_ids) == {a.label_id, b.label_id}


def test_three_labels_chain_merge_via_max_end(qapp):
    """C overlaps only via the extended max_end of the running group:
       A=[1,4], B=[2,6], C=[5,7]. C.start_time (5) > A.end_time (4)
       but <= max_end of {A,B} (6), so all three should merge."""
    store = LabelStore()
    groups = DerivedGroupModel(store)
    a = _make_dialogue(1.0, 4.0, text="A")
    b = _make_dialogue(2.0, 6.0, text="B")
    c = _make_dialogue(5.0, 7.0, text="C")
    _insert_dialogue(store, a)
    _insert_dialogue(store, b)
    _insert_dialogue(store, c)
    gs = groups.groups
    assert len(gs) == 1
    assert set(gs[0].label_ids) == {a.label_id, b.label_id, c.label_id}
    assert gs[0].start == pytest.approx(1.0)
    assert gs[0].end == pytest.approx(7.0)


def test_representative_time_picks_time_where_overlap_max(qapp):
    """For two overlapping labels, the chosen representative_time should be
    a time at which BOTH labels are visible."""
    store = LabelStore()
    groups = DerivedGroupModel(store)
    a = _make_dialogue(1.0, 3.0, text="A")  # midpoint 2.0
    b = _make_dialogue(2.0, 4.0, text="B")  # midpoint 3.0
    _insert_dialogue(store, a)
    _insert_dialogue(store, b)
    gs = groups.groups
    assert len(gs) == 1
    t = gs[0].representative_time
    # Both labels must be visible at the chosen time.
    assert a.start_time <= t <= a.end_time
    assert b.start_time <= t <= b.end_time
    # Must be one of the per-label midpoints (2.0 or 3.0). For this pair both
    # midpoints yield count=2; legacy ties go to the first iterated label, so
    # we expect 2.0 (A's midpoint).
    assert t == pytest.approx(2.0)


def test_representative_time_skips_outlier_midpoint(qapp):
    """If one label's midpoint sees only itself but another midpoint sees
    multiple labels, the latter should win."""
    store = LabelStore()
    groups = DerivedGroupModel(store)
    # A=[1,10] midpoint=5.5; B=[4,6] midpoint=5.0; C=[5,7] midpoint=6.0.
    # At t=5.5: A visible, B visible (4<=5.5<=6), C visible (5<=5.5<=7) -> 3.
    # At t=5.0: A,B,C all visible -> 3.  At t=6.0: A,B,C all visible -> 3.
    # All three midpoints tie at count=3; legacy picks the first iterated
    # label's midpoint (A's = 5.5).
    a = _make_dialogue(1.0, 10.0, text="A")
    b = _make_dialogue(4.0, 6.0, text="B")
    c = _make_dialogue(5.0, 7.0, text="C")
    _insert_dialogue(store, a)
    _insert_dialogue(store, b)
    _insert_dialogue(store, c)
    gs = groups.groups
    assert len(gs) == 1
    assert gs[0].representative_time == pytest.approx(5.5)


def test_group_id_stable_across_position_only_mutation(qapp):
    """Position-only mutations should not change group composition or id."""
    store = LabelStore()
    groups = DerivedGroupModel(store)
    store.load(AssFile.from_path(FIXTURE), source_path=FIXTURE)
    gid_before = groups.groups[0].group_id
    lid = store.state.order[0]
    store.apply(MoveLabel(label_id=lid, new_x=42.0, new_y=42.0))
    assert groups.groups[0].group_id == gid_before
