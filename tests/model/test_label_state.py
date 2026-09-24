"""Tests for LabelState — pure mutable state container."""

from pathlib import Path
import pytest

from sign_manager.model.ass_file import AssFile
from sign_manager.model.label_state import LabelState
from sign_manager.model.types import LabelId

FIXTURE = Path(__file__).parent.parent / "fixtures" / "sample.ass"


def _fresh_dialogue():
    """Build a fresh LabelDialogue by parsing the fixture and copying one label."""
    f = AssFile.from_path(FIXTURE)
    from dataclasses import replace
    return replace(f.labels[0], label_id="seed")  # any clone is fine for state tests


def test_empty_state():
    s = LabelState()
    assert s.labels == {}
    assert s.order == []
    assert s.styles == {}


def test_insert_then_lookup():
    s = LabelState()
    dlg = _fresh_dialogue()
    lid = LabelId("a")
    s.insert(lid, dlg, index=0)
    assert s.labels[lid] is dlg
    assert s.order == [lid]
    assert s.index_of(lid) == 0


def test_insert_duplicate_raises():
    s = LabelState()
    lid = LabelId("a")
    s.insert(lid, _fresh_dialogue(), index=0)
    with pytest.raises(ValueError):
        s.insert(lid, _fresh_dialogue(), index=0)


def test_remove_returns_snapshot_and_removes():
    s = LabelState()
    dlg = _fresh_dialogue()
    lid = LabelId("a")
    s.insert(lid, dlg, index=0)
    snap = s.remove(lid)
    assert snap.label_id == lid
    assert snap.dialogue is dlg
    assert snap.line_index == 0
    assert s.labels == {}
    assert s.order == []


def test_remove_missing_raises():
    s = LabelState()
    with pytest.raises(KeyError):
        s.remove(LabelId("missing"))


def test_replace_returns_old_and_updates():
    s = LabelState()
    a = _fresh_dialogue()
    b = _fresh_dialogue()
    lid = LabelId("x")
    s.insert(lid, a, index=0)
    old = s.replace(lid, b)
    assert old is a
    assert s.labels[lid] is b


def test_order_preserved_across_multiple_inserts():
    s = LabelState()
    ids = [LabelId(c) for c in "abc"]
    for i, lid in enumerate(ids):
        s.insert(lid, _fresh_dialogue(), index=i)
    assert s.order == ids


def test_insert_at_index():
    s = LabelState()
    s.insert(LabelId("a"), _fresh_dialogue(), index=0)
    s.insert(LabelId("c"), _fresh_dialogue(), index=1)
    # insert "b" between a and c
    s.insert(LabelId("b"), _fresh_dialogue(), index=1)
    assert s.order == [LabelId("a"), LabelId("b"), LabelId("c")]
