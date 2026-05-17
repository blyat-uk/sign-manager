"""Tests for the Mutation Protocol and BatchMutation."""

from sub_label_pos.model.label_state import LabelState
from sub_label_pos.model.mutations import BatchMutation
from sub_label_pos.model.types import LabelId


class _Dummy:
    """A minimal mutation for testing BatchMutation composition."""
    coalesce_key = None

    def __init__(self, marker: str):
        self.marker = marker

    def apply(self, state: LabelState) -> set[LabelId]:
        # Pretend to mutate by recording into state.labels with a sentinel value.
        state.labels[LabelId(self.marker)] = self.marker  # type: ignore[assignment]
        return {LabelId(self.marker)}

    def invert(self, state_before: LabelState) -> "_Dummy":
        return _Dummy("undo-" + self.marker)


def test_batch_apply_runs_children_in_order():
    s = LabelState()
    BatchMutation((_Dummy("a"), _Dummy("b"))).apply(s)
    assert set(s.labels.keys()) == {LabelId("a"), LabelId("b")}


def test_batch_apply_union_of_affected():
    s = LabelState()
    affected = BatchMutation((_Dummy("a"), _Dummy("b"), _Dummy("c"))).apply(s)
    assert affected == {LabelId("a"), LabelId("b"), LabelId("c")}


def test_batch_invert_reverses_and_inverts_each():
    s = LabelState()
    b = BatchMutation((_Dummy("a"), _Dummy("b")))
    inv = b.invert(s)
    assert isinstance(inv, BatchMutation)
    markers = [m.marker for m in inv.inner]
    assert markers == ["undo-b", "undo-a"]


def test_batch_inner_is_a_tuple_even_if_passed_list():
    b = BatchMutation([_Dummy("a"), _Dummy("b")])
    assert isinstance(b.inner, tuple)
    assert len(b.inner) == 2


def test_batch_coalesce_key_is_none():
    assert BatchMutation((_Dummy("a"),)).coalesce_key is None


def test_empty_batch_apply_returns_empty_set():
    s = LabelState()
    assert BatchMutation([]).apply(s) == set()


def test_empty_batch_invert_returns_empty_batch():
    s = LabelState()
    inv = BatchMutation([]).invert(s)
    assert isinstance(inv, BatchMutation)
    assert inv.inner == ()


def test_nested_batch_apply_runs_inner_batch_children():
    s = LabelState()
    inner_batch = BatchMutation([_Dummy("a"), _Dummy("b")])
    outer = BatchMutation([inner_batch, _Dummy("c")])
    affected = outer.apply(s)
    assert affected == {LabelId("a"), LabelId("b"), LabelId("c")}
    assert set(s.labels.keys()) == {LabelId("a"), LabelId("b"), LabelId("c")}


def test_nested_batch_invert_reverses_outer_only():
    """Outer batch reverses its inner; inner batch's own invert is also reversed."""
    s = LabelState()
    inner_batch = BatchMutation([_Dummy("a"), _Dummy("b")])
    outer = BatchMutation([inner_batch, _Dummy("c")])
    inv = outer.invert(s)
    # outer reversed: ['undo-c' Dummy, inner_batch.invert(s)]
    assert isinstance(inv, BatchMutation)
    assert len(inv.inner) == 2
    assert inv.inner[0].marker == "undo-c"   # first because reversed
    assert isinstance(inv.inner[1], BatchMutation)   # inverted inner batch
    # inner batch invert: reversed dummies
    inner_inv = inv.inner[1]
    assert [m.marker for m in inner_inv.inner] == ["undo-b", "undo-a"]
