"""Tests for ASS<->HTML<->segments rich-text conversions."""

from sign_manager.geometry.rich_text import (
    html_to_segments,
    parse_rich_text,
    segments_to_ass,
    segments_to_html,
)
from sign_manager.model.ass_file import TextSegment


def test_plain_text_roundtrip_through_segments():
    """Plain text (no formatting) round-trips cleanly."""
    text = "hello world"
    segs = parse_rich_text(text, default_bold=False, default_italic=False)
    assert segments_to_ass(segs, default_bold=False, default_italic=False) == text


def test_bold_segment_preserved():
    """Bold formatting survives segment conversion."""
    text = r"{\b1}bold text{\b0}"
    segs = parse_rich_text(text, default_bold=False, default_italic=False)
    bolds = [s for s in segs if s.bold]
    assert bolds, f"expected at least one bold segment, got {segs}"
    assert any(s.text == "bold text" and s.bold for s in segs)


def test_italic_segment_preserved():
    """Italic formatting survives segment conversion."""
    text = r"{\i1}italic text{\i0}"
    segs = parse_rich_text(text, default_bold=False, default_italic=False)
    assert any(s.text == "italic text" and s.italic for s in segs)


def test_segments_to_ass_minimal_overrides():
    """segments_to_ass emits minimal override blocks when default matches."""
    # Default bold=False; first segment is bold=False so no opening tag needed
    segs = [
        TextSegment("normal ", bold=False, italic=False),
        TextSegment("bold", bold=True, italic=False),
        TextSegment(" tail", bold=False, italic=False),
    ]
    out = segments_to_ass(segs, default_bold=False, default_italic=False)
    assert out == r"normal {\b1}bold{\b0} tail"


def test_html_roundtrip_preserves_bold():
    """HTML <-> segments <-> ASS round trip keeps bold."""
    original = r"{\b1}hi{\b0}"
    segs = parse_rich_text(original, default_bold=False, default_italic=False)
    html = segments_to_html(segs)
    back = segments_to_ass(
        html_to_segments(html), default_bold=False, default_italic=False
    )
    assert "hi" in back
    assert r"\b1" in back


def test_segments_to_html_escapes_special_chars():
    """HTML output escapes &, <, > in segment text."""
    segs = [TextSegment("a < b & c > d", bold=False, italic=False)]
    out = segments_to_html(segs)
    assert "&lt;" in out
    assert "&amp;" in out
    assert "&gt;" in out
    assert "<b>" not in out  # plain segment shouldn't get bold markup


def test_segments_to_html_bold_italic_wrap():
    """Bold + italic segment is wrapped in <b><i>."""
    segs = [TextSegment("x", bold=True, italic=True)]
    assert segments_to_html(segs) == "<b><i>x</i></b>"


def test_newline_preserved_in_segments_to_ass():
    """\\N survives ASS->segments->ASS."""
    text = r"line one\Nline two"
    segs = parse_rich_text(text, default_bold=False, default_italic=False)
    out = segments_to_ass(segs, default_bold=False, default_italic=False)
    assert r"\N" in out


def test_segments_to_html_converts_newline_to_br():
    """\\N in segment text becomes <br> for QTextEdit."""
    segs = [TextSegment(r"a\Nb", bold=False, italic=False)]
    assert segments_to_html(segs) == "a<br>b"


def test_html_to_segments_inherits_body_bold():
    """Qt's toHtml puts document-level bold on <body style="font-weight:700">.
    Text inside must be parsed as bold (else round-trip injects spurious {\\b0})."""
    qt_html = (
        '<html><head></head>'
        '<body style=" font-family:\'Arial\'; font-size:16pt; '
        'font-weight:700; font-style:normal;">'
        '<p>hello</p></body></html>'
    )
    segs = html_to_segments(qt_html)
    assert segs
    assert all(s.bold for s in segs if s.text)


def test_html_to_segments_inherits_body_italic():
    qt_html = (
        '<html><head></head>'
        '<body style=" font-family:\'Arial\'; font-size:16pt; '
        'font-weight:400; font-style:italic;">'
        '<p>hi</p></body></html>'
    )
    segs = html_to_segments(qt_html)
    assert segs
    assert all(s.italic for s in segs if s.text)


def test_qt_roundtrip_with_bold_style_emits_no_b0():
    """Reproduces the reported bug: editing a label whose effective style is
    bold (and clicking outside without changes) must not inject {\\b0}."""
    qt_html = (
        '<html><head></head>'
        '<body style=" font-family:\'Arial\'; font-size:16pt; '
        'font-weight:700; font-style:normal;">'
        '<p>hello world</p></body></html>'
    )
    segs = html_to_segments(qt_html)
    out = segments_to_ass(segs, default_bold=True, default_italic=False)
    assert "\\b" not in out
    assert "hello world" in out


def test_partial_unbold_inside_bold_body():
    """User unbolds the first word in an editor whose body font is bold."""
    qt_html = (
        '<html><head></head>'
        '<body style=" font-family:\'Arial\'; font-size:16pt; '
        'font-weight:700; font-style:normal;">'
        '<p><span style=" font-weight:400;">hello</span> world</p></body></html>'
    )
    segs = html_to_segments(qt_html)
    bold_text = "".join(s.text for s in segs if s.bold)
    unbold_text = "".join(s.text for s in segs if not s.bold)
    assert unbold_text == "hello"
    assert bold_text == " world"


def test_html_to_segments_handles_br():
    """<br> in HTML maps back to a \\N segment."""
    segs = html_to_segments("<p>a<br>b</p>")
    # Expect three segments: 'a', '\N', 'b' — but the merge step may join
    # adjacent same-format segments.
    joined = "".join(s.text for s in segs)
    assert r"\N" in joined
    assert "a" in joined and "b" in joined


def test_empty_rich_text_yields_empty_segment():
    """Empty rich_text input yields a single empty TextSegment (carrying defaults)."""
    segs = parse_rich_text("", default_bold=False, default_italic=True)
    assert len(segs) == 1
    assert segs[0].text == ""
    assert segs[0].italic is True


def test_default_bold_does_not_emit_tag():
    """When default_bold matches the segment, no \\b tag is emitted."""
    segs = [TextSegment("hi", bold=True, italic=False)]
    out = segments_to_ass(segs, default_bold=True, default_italic=False)
    assert out == "hi"


def test_re_export_from_ass_file_still_works():
    """Existing callers that import from model.ass_file keep working."""
    from sign_manager.model.ass_file import (
        parse_rich_text as p2,
        segments_to_ass as s2,
        segments_to_html as h2,
        html_to_segments as r2,
    )
    assert p2 is parse_rich_text
    assert s2 is segments_to_ass
    assert h2 is segments_to_html
    assert r2 is html_to_segments
