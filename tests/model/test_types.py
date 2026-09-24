from dataclasses import FrozenInstanceError
import pytest
from sign_manager.model.types import LabelId, LabelSnapshot, StylePatch


def test_style_patch_defaults_are_none():
    p = StylePatch()
    assert p.font_size is None
    assert p.primary_colour is None
    assert p.bold is None


def test_style_patch_is_frozen():
    p = StylePatch(font_size=24.0)
    with pytest.raises(FrozenInstanceError):
        p.font_size = 30.0


def test_style_patch_merge_overrides_only_set_fields():
    base = StylePatch(font_size=20.0, bold=True)
    over = StylePatch(font_size=30.0)
    merged = base.merge(over)
    assert merged.font_size == 30.0
    assert merged.bold is True


def test_label_id_is_distinct_type():
    lid: LabelId = LabelId("abc")
    # LabelId is a NewType; runtime value is just a str
    assert lid == "abc"


def test_new_label_id_returns_distinct_ids():
    from sign_manager.model.types import new_label_id
    ids = {new_label_id() for _ in range(100)}
    assert len(ids) == 100   # all unique


def test_new_label_id_has_prefix():
    from sign_manager.model.types import new_label_id
    lid = new_label_id()
    assert lid.startswith("LD-")
