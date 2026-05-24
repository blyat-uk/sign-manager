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


def test_from_state_preserves_non_label_dialogue_lines(tmp_path):
    """Non-label Dialogue lines (real subtitles, Comment lines) must survive a
    save round-trip even though they aren't tracked in LabelState.

    Regression: previously _save_ass discarded every Events-section line except
    labels, silently stripping the subtitle dialogue from the file.
    """
    from sub_label_pos.model.label_state import LabelState

    src = (
        "[Script Info]\n"
        "PlayResX: 1920\n"
        "PlayResY: 1080\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,40,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,5,10,10,10,1\n"
        "Style: Label,Arial,40,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,5,10,10,10,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Comment: 0,0:00:00.00,0:00:00.10,Default,,0,0,0,,a hand-written comment\n"
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,subtitle line one\n"
        "Dialogue: 0,0:00:01.00,0:00:03.00,Label,,0,0,0,,{\\pos(100,200)}Hello\n"
        "Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0,0,0,,subtitle line two\n"
        "Dialogue: 0,0:00:05.00,0:00:07.00,Label,,0,0,0,,{\\pos(500,200)}World\n"
        "Dialogue: 0,0:00:08.00,0:00:09.00,Default,,0,0,0,,trailing subtitle\n"
    )
    path = tmp_path / "mixed.ass"
    path.write_bytes(src.encode("utf-8-sig"))

    a = AssFile.from_path(path)
    assert len(a.labels) == 2, "fixture should have 2 labels"

    state = LabelState(
        labels={l.label_id: l for l in a.labels},
        order=[l.label_id for l in a.labels],
        styles=a.styles_by_name(),
    )
    rebuilt = AssFile.from_state(
        state,
        header_lines=a.lines[:a.events_start_index],
        non_label_event_lines=a.non_label_event_lines,
    )

    serialized = rebuilt.serialize().decode("utf-8-sig")

    assert "subtitle line one" in serialized, "subtitle dialogue must survive save"
    assert "subtitle line two" in serialized, "subtitle dialogue must survive save"
    assert "trailing subtitle" in serialized, "subtitle dialogue must survive save"
    assert "a hand-written comment" in serialized, "comment line must survive save"
    assert "Hello" in serialized and "World" in serialized, "labels must still be present"
