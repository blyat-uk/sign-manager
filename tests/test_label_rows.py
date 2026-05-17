from dataclasses import dataclass

from sub_label_pos.model.label_rows import (
    LabelGroupRow, group_labels_by_exact_timing,
)


@dataclass
class _FakeLabel:
    """Stand-in for LabelDialogue — only the fields the grouper reads."""
    start_time: float
    end_time: float
    text: str
    line_index: int = 0


def _lbls(*specs):
    return [_FakeLabel(s, e, t, i) for i, (s, e, t) in enumerate(specs)]


def test_empty_input_returns_empty_list():
    assert group_labels_by_exact_timing([]) == []


def test_single_label_makes_single_group():
    [lb] = _lbls((1.0, 2.0, "hello"))
    [row] = group_labels_by_exact_timing([lb])
    assert row.start_time == 1.0
    assert row.end_time == 2.0
    assert row.labels == (lb,)


def test_all_unique_timings_produce_separate_groups():
    a, b, c = _lbls((1.0, 2.0, "A"), (3.0, 4.0, "B"), (5.0, 6.0, "C"))
    rows = group_labels_by_exact_timing([a, b, c])
    assert [(r.start_time, r.end_time, r.labels) for r in rows] == [
        (1.0, 2.0, (a,)),
        (3.0, 4.0, (b,)),
        (5.0, 6.0, (c,)),
    ]


def test_identical_timings_group_into_one_row():
    a, b, c = _lbls((1.0, 2.0, "first"), (1.0, 2.0, "second"), (1.0, 2.0, "third"))
    [row] = group_labels_by_exact_timing([a, b, c])
    assert row.start_time == 1.0
    assert row.end_time == 2.0
    assert row.labels == (a, b, c)


def test_overlapping_but_not_identical_timings_stay_separate():
    a, b = _lbls((1.0, 3.0, "wide"), (2.0, 4.0, "narrow"))
    rows = group_labels_by_exact_timing([a, b])
    assert len(rows) == 2
    assert rows[0].labels == (a,)
    assert rows[1].labels == (b,)


def test_sort_order_is_chronological_then_by_end():
    later, earlier_short, earlier_long = _lbls(
        (5.0, 6.0, "late"),
        (1.0, 2.0, "early-short"),
        (1.0, 3.0, "early-long"),
    )
    rows = group_labels_by_exact_timing([later, earlier_short, earlier_long])
    assert [(r.start_time, r.end_time) for r in rows] == [
        (1.0, 2.0),
        (1.0, 3.0),
        (5.0, 6.0),
    ]


def test_within_group_label_order_matches_insertion_order():
    """Labels with identical timing keep their input-list order (which
    is the same as file/line order at the call site)."""
    third, first, second = _lbls(
        (1.0, 2.0, "third"),
        (1.0, 2.0, "first"),
        (1.0, 2.0, "second"),
    )
    [row] = group_labels_by_exact_timing([third, first, second])
    assert [lb.text for lb in row.labels] == ["third", "first", "second"]


def test_mixed_input_groups_and_singletons():
    a, b, c, d = _lbls(
        (1.0, 2.0, "alone"),
        (3.0, 4.0, "twin-a"),
        (3.0, 4.0, "twin-b"),
        (5.0, 6.0, "also-alone"),
    )
    rows = group_labels_by_exact_timing([a, b, c, d])
    assert len(rows) == 3
    assert rows[0].labels == (a,)
    assert rows[1].labels == (b, c)
    assert rows[2].labels == (d,)


def test_LabelGroupRow_is_immutable():
    """Frozen dataclass so callers can't accidentally mutate a row's labels."""
    import dataclasses
    a, b = _lbls((1.0, 2.0, "x"), (1.0, 2.0, "y"))
    [row] = group_labels_by_exact_timing([a, b])
    try:
        row.start_time = 99.0
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("LabelGroupRow should be frozen")
