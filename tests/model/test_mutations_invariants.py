"""apply -> invert -> apply equals apply: the round-trip invariant.

For each mutation type, verify that applying it, then its inverse, then it again
produces the same final state as applying it just once. This is a single
safety net that protects against regressions across all mutations.
"""

from dataclasses import replace
from pathlib import Path

import pytest

from sign_manager.model.ass_file import AssFile
from sign_manager.model.label_state import LabelState
from sign_manager.model.mutations import (
    MoveLabel, ResizeLabel, RotateLabel, EditText, ChangeStyle, PasteStyle,
    RetimeLabel, DeleteLabel, InsertLabel, DuplicateLabel,
    MergeLabels, SplitMerged,
)
from sign_manager.model.types import LabelId, LabelSnapshot, StylePatch, new_label_id

FIXTURE = Path(__file__).parent.parent / "fixtures" / "sample.ass"


def _fresh_state() -> LabelState:
    """A fresh, isolated LabelState from the fixture."""
    ass = AssFile.from_path(FIXTURE)
    return LabelState(
        labels={l.label_id: replace(l) for l in ass.labels},
        order=[l.label_id for l in ass.labels],
        styles=ass.styles_by_name(),
    )


def _state_signature(s: LabelState):
    """Comparable signature: order list + per-label key field tuples.
    Used to assert state equivalence after apply -> invert -> apply -> compared
    against a fresh apply."""
    return [
        (
            lid,
            s.labels[lid].pos_x,
            s.labels[lid].pos_y,
            s.labels[lid].font_size,
            s.labels[lid].rotation,
            s.labels[lid].start_time,
            s.labels[lid].end_time,
            s.labels[lid].text,
            s.labels[lid].rich_text,
            s.labels[lid].bold,
            s.labels[lid].italic,
            s.labels[lid].primary_colour,
            s.labels[lid].outline_colour,
            s.labels[lid].outline_width,
            s.labels[lid].alignment,
        )
        for lid in s.order
    ]


# --- Per-mutation factories ---------------------------------------------
# Each factory builds a Mutation from a fresh state. We then verify the
# invariant against TWO independent states.


def _mk_move(s):
    return MoveLabel(label_id=s.order[0], new_x=99.0, new_y=42.0)


def _mk_resize(s):
    return ResizeLabel(label_id=s.order[0], new_font_size=77)


def _mk_rotate(s):
    return RotateLabel(label_id=s.order[0], new_angle=33.0)


def _mk_edit_text(s):
    return EditText(label_id=s.order[0], new_text="X", new_rich_text=r"{\b1}X{\b0}")


def _mk_change_style(s):
    return ChangeStyle(label_id=s.order[0], patch=StylePatch(font_size=88, bold=True))


def _mk_paste_style(s):
    return PasteStyle(label_id=s.order[0], patch=StylePatch(bold=True))


def _mk_retime(s):
    return RetimeLabel(label_id=s.order[0], new_start=99.0, new_end=100.0)


def _mk_delete(s):
    return DeleteLabel.from_state(s, s.order[0])


def _mk_duplicate(s):
    # Use a consistent ID for both forward and reverse paths
    dup_id = LabelId("dup-inv-test")
    return DuplicateLabel(
        source_label_id=s.order[0], new_label_id=dup_id, position_offset=(10.0, 5.0),
    )


def _mk_insert(s):
    """Insert needs a snapshot — build it from a still-present label, then
    delete that label so the insert is a meaningful round-trip."""
    target = s.order[0]
    dlg = s.labels[target]
    snap = LabelSnapshot(label_id=target, dialogue=dlg, line_index=0)
    s.remove(target)   # mutate fresh state so insert can re-add it
    return InsertLabel(snapshot=snap)


def _mk_merge(s):
    src_ids = list(s.order[:2])
    snaps = tuple(
        LabelSnapshot(label_id=l, dialogue=s.labels[l], line_index=s.index_of(l))
        for l in src_ids
    )
    merged_id = LabelId("merged-inv-test")
    merged_dlg = replace(snaps[0].dialogue, label_id=merged_id)
    return MergeLabels(source_snapshots=snaps, merged_id=merged_id, merged_dialogue=merged_dlg)


def _mk_split_merged(s):
    """SplitMerged inversion has documented limitations (cannot perfectly
    reconstruct merged_dialogue). For this invariant test, we merge first,
    then verify split-then-undo round-trips against a second merge."""
    src_ids = list(s.order[:2])
    snaps = tuple(
        LabelSnapshot(label_id=l, dialogue=s.labels[l], line_index=s.index_of(l))
        for l in src_ids
    )
    merged_id = LabelId("split-merged-inv-test")
    merged_dlg = replace(snaps[0].dialogue, label_id=merged_id)
    # Apply merge so split has something to undo
    MergeLabels(source_snapshots=snaps, merged_id=merged_id, merged_dialogue=merged_dlg).apply(s)
    return SplitMerged(source_snapshots=snaps, merged_id=merged_id)


@pytest.mark.parametrize("make_mutation", [
    _mk_move,
    _mk_resize,
    _mk_rotate,
    _mk_edit_text,
    _mk_change_style,
    _mk_paste_style,
    _mk_retime,
    _mk_delete,
    _mk_duplicate,
    _mk_insert,
    _mk_merge,
    _mk_split_merged,
])
def test_apply_invert_apply_equivalent_to_apply(make_mutation):
    """For every mutation type: applying once == applying, undoing, reapplying."""
    s1 = _fresh_state()
    s2 = _fresh_state()
    m1 = make_mutation(s1)
    m2 = make_mutation(s2)
    # Snapshot s1 BEFORE applying for the invert call
    s1_before = LabelState(
        labels=dict(s1.labels), order=list(s1.order), styles=dict(s1.styles),
    )
    inv = m1.invert(s1_before)
    m1.apply(s1)
    inv.apply(s1)
    m1.apply(s1)   # forward again
    m2.apply(s2)   # baseline: just one forward
    assert _state_signature(s1) == _state_signature(s2), \
        f"Round-trip failed for {make_mutation.__name__}"
