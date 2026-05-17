"""Tests for the save flow: mutate via store, save via AssFile.from_state,
re-load from disk, verify the mutation persisted.
"""

import shutil
from pathlib import Path

from sub_label_pos.model.ass_file import AssFile
from sub_label_pos.model.label_store import LabelStore
from sub_label_pos.model.mutations import MoveLabel, ResizeLabel

FIXTURE = Path(__file__).parent.parent / "fixtures" / "sample.ass"


def test_save_state_via_from_state_reflects_position_mutation(qapp, tmp_path):
    """Load, mutate position, save via from_state(), re-load; mutation persists."""
    work = tmp_path / "work.ass"
    shutil.copy(FIXTURE, work)

    store = LabelStore()
    ass = AssFile.from_path(work)
    store.load(ass, source_path=work)

    lid = store.state.order[0]
    store.apply(MoveLabel(label_id=lid, new_x=999, new_y=42))

    header = ass.lines[:ass.events_start_index]
    rebuilt = AssFile.from_state(
        store.state,
        header_lines=header,
        play_res_x=ass.play_res_x,
        play_res_y=ass.play_res_y,
    )
    work.write_bytes(rebuilt.serialize())

    reloaded = AssFile.from_path(work)
    assert reloaded.labels[0].pos_x == 999
    assert reloaded.labels[0].pos_y == 42


def test_save_state_preserves_label_count_and_order(qapp, tmp_path):
    """Saving without any mutations is a round-trip; all labels survive."""
    work = tmp_path / "work.ass"
    shutil.copy(FIXTURE, work)

    store = LabelStore()
    ass = AssFile.from_path(work)
    store.load(ass, source_path=work)

    rebuilt = AssFile.from_state(
        store.state,
        header_lines=ass.lines[:ass.events_start_index],
        play_res_x=ass.play_res_x,
        play_res_y=ass.play_res_y,
    )
    work.write_bytes(rebuilt.serialize())

    reloaded = AssFile.from_path(work)
    assert len(reloaded.labels) == len(ass.labels)
    assert [l.text for l in reloaded.labels] == [l.text for l in ass.labels]


def test_save_state_reflects_resize_mutation(qapp, tmp_path):
    """Resize via store, save, reload; font size persists."""
    work = tmp_path / "work.ass"
    shutil.copy(FIXTURE, work)

    store = LabelStore()
    ass = AssFile.from_path(work)
    store.load(ass, source_path=work)

    lid = store.state.order[0]
    store.apply(ResizeLabel(label_id=lid, new_font_size=77))

    rebuilt = AssFile.from_state(
        store.state,
        header_lines=ass.lines[:ass.events_start_index],
        play_res_x=ass.play_res_x,
        play_res_y=ass.play_res_y,
    )
    work.write_bytes(rebuilt.serialize())

    reloaded = AssFile.from_path(work)
    assert reloaded.labels[0].font_size == 77
