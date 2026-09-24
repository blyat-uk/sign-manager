"""Tests for undo/redo UI wiring."""

from pathlib import Path

from sign_manager.model.ass_file import AssFile
from sign_manager.model.label_store import LabelStore
from sign_manager.model.mutations import MoveLabel

FIXTURE = Path(__file__).parent.parent / "fixtures" / "sample.ass"


def test_can_undo_signal_fires_on_apply(qapp):
    store = LabelStore()
    store.load(AssFile.from_path(FIXTURE), source_path=FIXTURE)
    received = []
    store.undo_stack.can_undo_changed.connect(received.append)
    lid = store.state.order[0]
    store.apply(MoveLabel(label_id=lid, new_x=1, new_y=2))
    assert received == [True]


def test_can_redo_signal_fires_on_undo(qapp):
    store = LabelStore()
    store.load(AssFile.from_path(FIXTURE), source_path=FIXTURE)
    lid = store.state.order[0]
    store.apply(MoveLabel(label_id=lid, new_x=1, new_y=2))
    received = []
    store.undo_stack.can_redo_changed.connect(received.append)
    store.undo()
    assert received == [True]
