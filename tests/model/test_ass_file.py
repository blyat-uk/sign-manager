from pathlib import Path
from sub_label_pos.model.ass_file import AssFile

FIXTURE = Path(__file__).parent.parent / "fixtures" / "sample.ass"


def test_parse_assigns_label_ids():
    f = AssFile.from_path(FIXTURE)
    ids = [lbl.label_id for lbl in f.labels]
    assert all(ids), "every label must have a non-empty LabelId"
    assert len(set(ids)) == len(ids), "label IDs must be unique"


def test_label_ids_stable_across_reparse():
    """The same source bytes must produce the same IDs."""
    a = AssFile.from_path(FIXTURE)
    b = AssFile.from_path(FIXTURE)
    assert [l.label_id for l in a.labels] == [l.label_id for l in b.labels]


def test_serialize_roundtrip_preserves_labels():
    """Parse + serialize + reparse preserves the label list (text and order)."""
    f = AssFile.from_path(FIXTURE)
    out = f.serialize()
    f2 = AssFile.from_bytes(out)
    assert [l.text for l in f.labels] == [l.text for l in f2.labels]

    # Label IDs must survive the roundtrip (same input bytes -> same IDs)
    assert [l.label_id for l in f.labels] == [l.label_id for l in f2.labels]


def test_label_by_id_lookup():
    f = AssFile.from_path(FIXTURE)
    first = f.labels[0]
    assert f.label_by_id(first.label_id) is first


def test_from_state_roundtrip_preserves_labels():
    """Build an AssFile from a parsed state; the result should re-parse identically."""
    from sub_label_pos.model.label_state import LabelState
    a = AssFile.from_path(FIXTURE)
    state = LabelState(
        labels={l.label_id: l for l in a.labels},
        order=[l.label_id for l in a.labels],
        styles=a.styles_by_name(),
    )
    rebuilt = AssFile.from_state(state, header_lines=a.lines[:a.events_start_index])
    # Re-parse the serialized form
    rebuilt2 = AssFile.from_bytes(rebuilt.serialize())
    assert len(rebuilt2.labels) == len(a.labels)
    assert [l.text for l in rebuilt2.labels] == [l.text for l in a.labels]
    assert [(l.pos_x, l.pos_y) for l in rebuilt2.labels] == [
        (l.pos_x, l.pos_y) for l in a.labels
    ]
    assert [l.style_name for l in rebuilt2.labels] == [l.style_name for l in a.labels]
    # Style section preserved.
    assert set(rebuilt2.styles.keys()) == set(a.styles.keys())


def test_from_state_default_header():
    """Without an explicit header, from_state synthesizes a usable one."""
    from sub_label_pos.model.label_state import LabelState
    a = AssFile.from_path(FIXTURE)
    state = LabelState(
        labels={l.label_id: l for l in a.labels},
        order=[l.label_id for l in a.labels],
        styles=a.styles_by_name(),
    )
    rebuilt = AssFile.from_state(state)  # no header_lines
    rebuilt2 = AssFile.from_bytes(rebuilt.serialize())
    # Labels still round-trip even with a synthesized header.
    assert [l.text for l in rebuilt2.labels] == [l.text for l in a.labels]
    assert [(l.pos_x, l.pos_y) for l in rebuilt2.labels] == [
        (l.pos_x, l.pos_y) for l in a.labels
    ]


def test_from_state_reflects_position_mutation():
    """A mutation applied via the state is reflected in the serialized output."""
    from sub_label_pos.model.label_state import LabelState
    from dataclasses import replace
    a = AssFile.from_path(FIXTURE)
    state = LabelState(
        labels={l.label_id: l for l in a.labels},
        order=[l.label_id for l in a.labels],
        styles=a.styles_by_name(),
    )
    lid = state.order[0]
    state.labels[lid] = replace(state.labels[lid], pos_x=999, pos_y=42)
    rebuilt = AssFile.from_state(state, header_lines=a.lines[:a.events_start_index])
    rebuilt2 = AssFile.from_bytes(rebuilt.serialize())
    assert rebuilt2.labels[0].pos_x == 999
    assert rebuilt2.labels[0].pos_y == 42
