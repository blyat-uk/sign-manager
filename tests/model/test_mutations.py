"""Tests for concrete mutation classes."""

from dataclasses import replace as dc_replace
from pathlib import Path
import pytest

from sub_label_pos.model.ass_file import AssFile
from sub_label_pos.model.label_state import LabelState
from sub_label_pos.model.mutations import (
    MoveLabel, ResizeLabel, RotateLabel, EditText, ChangeStyle, PasteStyle,
    RetimeLabel, DeleteLabel, InsertLabel, DuplicateLabel,
    MergeLabels, SplitMerged,
)
from sub_label_pos.model.types import LabelSnapshot, StylePatch, LabelId, new_label_id

FIXTURE = Path(__file__).parent.parent / "fixtures" / "sample.ass"


@pytest.fixture
def state():
    """A fresh LabelState populated from the sample fixture."""
    ass = AssFile.from_path(FIXTURE)
    return LabelState(
        labels={l.label_id: l for l in ass.labels},
        order=[l.label_id for l in ass.labels],
        styles=ass.styles_by_name(),
    )


def test_move_label_apply_updates_position(state):
    lid = state.order[0]
    affected = MoveLabel(label_id=lid, new_x=999.0, new_y=42.0).apply(state)
    assert affected == {lid}
    assert state.labels[lid].pos_x == 999.0
    assert state.labels[lid].pos_y == 42.0


def test_move_label_preserves_other_fields(state):
    lid = state.order[0]
    before = state.labels[lid]
    MoveLabel(label_id=lid, new_x=999.0, new_y=42.0).apply(state)
    after = state.labels[lid]
    # All non-position fields should be unchanged
    assert after.text == before.text
    assert after.font_size == before.font_size
    assert after.start_time == before.start_time
    assert after.end_time == before.end_time
    assert after.label_id == before.label_id


def test_move_label_invert_restores(state):
    lid = state.order[0]
    before_x = state.labels[lid].pos_x
    before_y = state.labels[lid].pos_y
    m = MoveLabel(label_id=lid, new_x=999.0, new_y=42.0)
    inv = m.invert(state)   # snapshot inverse against pre-apply state
    m.apply(state)
    inv.apply(state)
    assert state.labels[lid].pos_x == before_x
    assert state.labels[lid].pos_y == before_y


def test_move_label_coalesce_key_per_label(state):
    lid = state.order[0]
    assert MoveLabel(label_id=lid, new_x=1.0, new_y=2.0).coalesce_key == f"move:{lid}"


def test_move_label_does_not_mutate_in_place(state):
    """Critical: applying must produce a new LabelDialogue instance via replace,
    so that any external reference to the pre-mutation object remains intact
    (the undo snapshot relies on this)."""
    lid = state.order[0]
    before = state.labels[lid]
    MoveLabel(label_id=lid, new_x=999.0, new_y=42.0).apply(state)
    after = state.labels[lid]
    assert before is not after, "apply must replace the dataclass, not mutate in place"
    # The pre-mutation reference still has the OLD position:
    assert before.pos_x != 999.0


def test_resize_label_apply_updates_font_size(state):
    lid = state.order[0]
    affected = ResizeLabel(label_id=lid, new_font_size=99).apply(state)
    assert affected == {lid}
    assert state.labels[lid].font_size == 99


def test_resize_label_invert_restores(state):
    lid = state.order[0]
    before = state.labels[lid].font_size
    m = ResizeLabel(label_id=lid, new_font_size=99)
    inv = m.invert(state)
    m.apply(state)
    inv.apply(state)
    assert state.labels[lid].font_size == before


def test_resize_label_coalesce_key(state):
    lid = state.order[0]
    assert ResizeLabel(label_id=lid, new_font_size=99).coalesce_key == f"resize:{lid}"


def test_resize_label_does_not_mutate_in_place(state):
    lid = state.order[0]
    before = state.labels[lid]
    ResizeLabel(label_id=lid, new_font_size=99).apply(state)
    after = state.labels[lid]
    assert before is not after
    assert before.font_size != 99


def test_rotate_label_apply_updates_angle(state):
    lid = state.order[0]
    affected = RotateLabel(label_id=lid, new_angle=45.0).apply(state)
    assert affected == {lid}
    assert state.labels[lid].rotation == 45.0


def test_rotate_label_invert_restores(state):
    lid = state.order[0]
    before = state.labels[lid].rotation
    m = RotateLabel(label_id=lid, new_angle=45.0)
    inv = m.invert(state)
    m.apply(state)
    inv.apply(state)
    assert state.labels[lid].rotation == before


def test_rotate_label_coalesce_key(state):
    lid = state.order[0]
    assert RotateLabel(label_id=lid, new_angle=45.0).coalesce_key == f"rotate:{lid}"


def test_rotate_label_does_not_mutate_in_place(state):
    lid = state.order[0]
    before = state.labels[lid]
    RotateLabel(label_id=lid, new_angle=45.0).apply(state)
    after = state.labels[lid]
    assert before is not after
    assert before.rotation != 45.0


def test_edit_text_apply_updates_both_text_fields(state):
    lid = state.order[0]
    affected = EditText(label_id=lid, new_text="Replaced", new_rich_text="Replaced").apply(state)
    assert affected == {lid}
    assert state.labels[lid].text == "Replaced"
    assert state.labels[lid].rich_text == "Replaced"


def test_edit_text_preserves_position(state):
    lid = state.order[0]
    before_x = state.labels[lid].pos_x
    EditText(label_id=lid, new_text="X", new_rich_text="X").apply(state)
    assert state.labels[lid].pos_x == before_x


def test_edit_text_invert_restores(state):
    lid = state.order[0]
    before_text = state.labels[lid].text
    before_rich = state.labels[lid].rich_text
    m = EditText(label_id=lid, new_text="X", new_rich_text=r"{\b1}X{\b0}")
    inv = m.invert(state)
    m.apply(state)
    inv.apply(state)
    assert state.labels[lid].text == before_text
    assert state.labels[lid].rich_text == before_rich


def test_edit_text_coalesce_key(state):
    lid = state.order[0]
    assert EditText(label_id=lid, new_text="x", new_rich_text="x").coalesce_key == f"edit_text:{lid}"


def test_edit_text_does_not_mutate_in_place(state):
    lid = state.order[0]
    before = state.labels[lid]
    EditText(label_id=lid, new_text="Different", new_rich_text="Different").apply(state)
    after = state.labels[lid]
    assert before is not after
    assert before.text != "Different"


def test_change_style_applies_only_set_fields(state):
    lid = state.order[0]
    before_outline = state.labels[lid].outline_colour
    patch = StylePatch(font_size=50, bold=True)   # outline_colour intentionally None
    ChangeStyle(label_id=lid, patch=patch).apply(state)
    assert state.labels[lid].font_size == 50
    assert state.labels[lid].bold is True
    assert state.labels[lid].outline_colour == before_outline   # unchanged


def test_change_style_invert_captures_prior_values(state):
    lid = state.order[0]
    before_size = state.labels[lid].font_size
    before_bold = state.labels[lid].bold
    patch = StylePatch(font_size=99, bold=True)
    m = ChangeStyle(label_id=lid, patch=patch)
    inv = m.invert(state)
    m.apply(state)
    inv.apply(state)
    assert state.labels[lid].font_size == before_size
    assert state.labels[lid].bold == before_bold


def test_change_style_coalesce_key_is_none(state):
    lid = state.order[0]
    assert ChangeStyle(label_id=lid, patch=StylePatch()).coalesce_key is None


def test_change_style_empty_patch_is_noop(state):
    lid = state.order[0]
    before = state.labels[lid]
    ChangeStyle(label_id=lid, patch=StylePatch()).apply(state)
    after = state.labels[lid]
    # No fields to change — but apply still produces a new instance via replace(dlg).
    # That's fine; the values are identical.
    assert after.font_size == before.font_size


def test_change_style_ignores_font_name_field(state):
    lid = state.order[0]
    # font_name is in StylePatch but not on LabelDialogue; should be ignored without error
    ChangeStyle(label_id=lid, patch=StylePatch(font_name="Times")).apply(state)
    # No assertion error here; if it didn't raise, we're good.


def test_change_style_does_not_mutate_in_place(state):
    lid = state.order[0]
    before = state.labels[lid]
    ChangeStyle(label_id=lid, patch=StylePatch(font_size=77)).apply(state)
    after = state.labels[lid]
    assert before is not after
    assert before.font_size != 77


def test_paste_style_behaves_like_change_style(state):
    lid = state.order[0]
    PasteStyle(label_id=lid, patch=StylePatch(bold=True)).apply(state)
    assert state.labels[lid].bold is True


def test_paste_style_is_distinct_type(state):
    # Subclass so isinstance(PasteStyle, ChangeStyle) is True but the runtime class differs
    p = PasteStyle(label_id=state.order[0], patch=StylePatch(bold=True))
    assert isinstance(p, ChangeStyle)
    assert type(p) is PasteStyle


def test_retime_label_apply_updates_times(state):
    lid = state.order[0]
    affected = RetimeLabel(label_id=lid, new_start=5.0, new_end=10.0).apply(state)
    assert affected == {lid}
    assert state.labels[lid].start_time == 5.0
    assert state.labels[lid].end_time == 10.0


def test_retime_label_invert_restores(state):
    lid = state.order[0]
    before_start = state.labels[lid].start_time
    before_end = state.labels[lid].end_time
    m = RetimeLabel(label_id=lid, new_start=99.0, new_end=100.0)
    inv = m.invert(state)
    m.apply(state)
    inv.apply(state)
    assert state.labels[lid].start_time == before_start
    assert state.labels[lid].end_time == before_end


def test_retime_label_coalesce_key_includes_label_id(state):
    lid = state.order[0]
    m = RetimeLabel(label_id=lid, new_start=0.0, new_end=1.0)
    assert m.coalesce_key == f"retime:{lid}"


def test_retime_label_coalesce_key_override_takes_precedence(state):
    lid = state.order[0]
    m = RetimeLabel(
        label_id=lid, new_start=0.0, new_end=1.0,
        coalesce_key_override="retime-drag:abc123",
    )
    assert m.coalesce_key == "retime-drag:abc123"


def test_retime_label_does_not_mutate_in_place(state):
    lid = state.order[0]
    before = state.labels[lid]
    RetimeLabel(label_id=lid, new_start=99.0, new_end=100.0).apply(state)
    after = state.labels[lid]
    assert before is not after
    assert before.start_time != 99.0


def test_delete_label_removes_from_state(state):
    lid = state.order[0]
    m = DeleteLabel.from_state(state, lid)
    affected = m.apply(state)
    assert affected == {lid}
    assert lid not in state.labels
    assert lid not in state.order


def test_delete_label_snapshot_carries_full_record(state):
    lid = state.order[0]
    before = state.labels[lid]
    before_idx = state.index_of(lid)
    m = DeleteLabel.from_state(state, lid)
    assert m.snapshot.label_id == lid
    assert m.snapshot.dialogue is before
    assert m.snapshot.line_index == before_idx


def test_delete_label_invert_reinserts(state):
    lid = state.order[0]
    before = state.labels[lid]
    before_idx = state.index_of(lid)
    m = DeleteLabel.from_state(state, lid)
    inv = m.invert(state)
    m.apply(state)
    inv.apply(state)
    assert state.labels[lid] is before
    assert state.index_of(lid) == before_idx


def test_delete_label_coalesce_key_is_none(state):
    lid = state.order[0]
    assert DeleteLabel.from_state(state, lid).coalesce_key is None


def test_insert_label_adds_to_state(state):
    lid = state.order[0]
    deleted = DeleteLabel.from_state(state, lid)
    deleted.apply(state)
    inv = InsertLabel(snapshot=deleted.snapshot)
    affected = inv.apply(state)
    assert affected == {lid}
    assert lid in state.labels


def test_insert_label_invert_deletes(state):
    lid = state.order[0]
    deleted = DeleteLabel.from_state(state, lid)
    deleted.apply(state)
    ins = InsertLabel(snapshot=deleted.snapshot)
    inv = ins.invert(state)   # state has no lid right now
    ins.apply(state)          # reinsert
    inv.apply(state)          # delete again
    assert lid not in state.labels


def test_insert_label_clamps_index_when_too_large(state):
    """If the snapshot's line_index exceeds the current state length,
    InsertLabel should append rather than raise."""
    lid = state.order[-1]
    snap = LabelSnapshot(label_id=lid, dialogue=state.labels[lid], line_index=999)
    # Remove the existing label so we can re-insert via snapshot
    state.remove(lid)
    InsertLabel(snapshot=snap).apply(state)
    assert lid in state.labels
    assert state.order[-1] == lid


def test_duplicate_label_creates_new_label(state):
    src = state.order[0]
    new_id = LabelId("dup-1")
    affected = DuplicateLabel(
        source_label_id=src,
        new_label_id=new_id,
        position_offset=(10.0, 5.0),
    ).apply(state)
    assert affected == {new_id}
    assert new_id in state.labels


def test_duplicate_label_applies_position_offset(state):
    src = state.order[0]
    src_dlg = state.labels[src]
    new_id = LabelId("dup-1")
    DuplicateLabel(
        source_label_id=src,
        new_label_id=new_id,
        position_offset=(10.0, 5.0),
    ).apply(state)
    new_dlg = state.labels[new_id]
    assert new_dlg.pos_x == src_dlg.pos_x + 10
    assert new_dlg.pos_y == src_dlg.pos_y + 5


def test_duplicate_label_preserves_other_fields(state):
    src = state.order[0]
    src_dlg = state.labels[src]
    new_id = LabelId("dup-1")
    DuplicateLabel(
        source_label_id=src,
        new_label_id=new_id,
        position_offset=(0, 0),
    ).apply(state)
    new_dlg = state.labels[new_id]
    assert new_dlg.text == src_dlg.text
    assert new_dlg.font_size == src_dlg.font_size
    assert new_dlg.label_id == new_id   # new id, not the source id


def test_duplicate_label_inserted_after_source(state):
    src = state.order[0]
    src_idx = state.index_of(src)
    new_id = LabelId("dup-1")
    DuplicateLabel(
        source_label_id=src,
        new_label_id=new_id,
        position_offset=(0, 0),
    ).apply(state)
    assert state.index_of(new_id) == src_idx + 1


def test_duplicate_label_invert_deletes(state):
    src = state.order[0]
    new_id = LabelId("dup-1")
    m = DuplicateLabel(
        source_label_id=src,
        new_label_id=new_id,
        position_offset=(10.0, 5.0),
    )
    inv = m.invert(state)  # state has NO new_id yet
    m.apply(state)
    assert new_id in state.labels
    inv.apply(state)
    assert new_id not in state.labels


def test_duplicate_label_coalesce_key_is_none(state):
    assert DuplicateLabel(
        source_label_id=state.order[0],
        new_label_id=LabelId("d"),
        position_offset=(0, 0),
    ).coalesce_key is None


def test_duplicate_with_new_label_id_helper(state):
    src = state.order[0]
    new_id = new_label_id()
    assert new_id.startswith("LD-")
    DuplicateLabel(
        source_label_id=src,
        new_label_id=new_id,
        position_offset=(0, 0),
    ).apply(state)
    assert new_id in state.labels


def _make_merged_dlg(src_dlg, merged_id):
    """Helper to build a merged LabelDialogue for tests.
    Mimics what widget code would do: take first source as template, override id."""
    return dc_replace(src_dlg, label_id=merged_id)


def test_merge_removes_sources_and_inserts_merged(state):
    src_ids = list(state.order[:2])
    snaps = [
        LabelSnapshot(label_id=l, dialogue=state.labels[l], line_index=state.index_of(l))
        for l in src_ids
    ]
    merged_id = LabelId("merged-1")
    merged_dlg = _make_merged_dlg(snaps[0].dialogue, merged_id)
    affected = MergeLabels(
        source_snapshots=tuple(snaps),
        merged_id=merged_id,
        merged_dialogue=merged_dlg,
    ).apply(state)
    # Removed sources, added merged
    for s in snaps:
        assert s.label_id not in state.labels
    assert merged_id in state.labels
    # affected set: every id involved
    assert affected == {merged_id} | {s.label_id for s in snaps}


def test_merge_inserts_at_earliest_source_index(state):
    src_ids = list(state.order[:2])
    snaps = [
        LabelSnapshot(label_id=l, dialogue=state.labels[l], line_index=state.index_of(l))
        for l in src_ids
    ]
    earliest_idx = min(s.line_index for s in snaps)
    merged_id = LabelId("merged-1")
    merged_dlg = _make_merged_dlg(snaps[0].dialogue, merged_id)
    MergeLabels(
        source_snapshots=tuple(snaps),
        merged_id=merged_id,
        merged_dialogue=merged_dlg,
    ).apply(state)
    assert state.index_of(merged_id) == earliest_idx


def test_merge_invert_restores_sources(state):
    src_ids = list(state.order[:2])
    snaps = [
        LabelSnapshot(label_id=l, dialogue=state.labels[l], line_index=state.index_of(l))
        for l in src_ids
    ]
    merged_id = LabelId("merged-1")
    merged_dlg = _make_merged_dlg(snaps[0].dialogue, merged_id)
    m = MergeLabels(
        source_snapshots=tuple(snaps),
        merged_id=merged_id,
        merged_dialogue=merged_dlg,
    )
    inv = m.invert(state)
    m.apply(state)
    inv.apply(state)
    for s in snaps:
        assert s.label_id in state.labels
    assert merged_id not in state.labels


def test_split_merged_apply_removes_merged_and_restores_sources(state):
    src_ids = list(state.order[:2])
    snaps = [
        LabelSnapshot(label_id=l, dialogue=state.labels[l], line_index=state.index_of(l))
        for l in src_ids
    ]
    merged_id = LabelId("merged-1")
    merged_dlg = _make_merged_dlg(snaps[0].dialogue, merged_id)
    # Pre-condition: apply a merge first so split has something to undo
    MergeLabels(
        source_snapshots=tuple(snaps), merged_id=merged_id, merged_dialogue=merged_dlg,
    ).apply(state)
    # Now split:
    affected = SplitMerged(source_snapshots=tuple(snaps), merged_id=merged_id).apply(state)
    for s in snaps:
        assert s.label_id in state.labels
    assert merged_id not in state.labels


def test_merge_coalesce_key_is_none(state):
    src_ids = list(state.order[:2])
    snaps = [
        LabelSnapshot(label_id=l, dialogue=state.labels[l], line_index=state.index_of(l))
        for l in src_ids
    ]
    merged_id = LabelId("merged-1")
    merged_dlg = _make_merged_dlg(snaps[0].dialogue, merged_id)
    m = MergeLabels(
        source_snapshots=tuple(snaps), merged_id=merged_id, merged_dialogue=merged_dlg,
    )
    assert m.coalesce_key is None
