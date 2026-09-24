"""Pure ASS<->HTML<->segments conversions for rich (bold/italic) text.

These functions translate between three representations of label text:

  - **ASS rich text**: the raw subtitle text with inline override blocks like
    ``{\\b1}bold{\\b0} normal``.
  - **TextSegment list**: a flat list of ``(text, bold, italic)`` records used
    by the renderer and inline editor.
  - **HTML**: the format used by Qt's ``QTextEdit`` for inline editing.

The implementations are preserved byte-for-byte from the original
``model.ass_file`` versions; only their home moved.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

from sign_manager.model.text_segment import TextSegment

_B_TAG_RE = re.compile(r"\\b(\d)")
_I_TAG_RE = re.compile(r"\\i(\d)")


def parse_rich_text(rich_text: str, default_bold: bool, default_italic: bool) -> list[TextSegment]:
    """Parse rich_text (with inline override blocks) into TextSegments."""
    segments: list[TextSegment] = []
    cur_bold = default_bold
    cur_italic = default_italic
    pos = 0
    text = rich_text

    while pos < len(text):
        if text[pos] == '{':
            end = text.find('}', pos)
            if end == -1:
                # No closing brace, treat rest as text
                segments.append(TextSegment(text[pos:], cur_bold, cur_italic))
                break
            block = text[pos:end + 1]
            # Process bold/italic tags in this block
            for bm in _B_TAG_RE.finditer(block):
                cur_bold = bm.group(1) != '0'
            for im in _I_TAG_RE.finditer(block):
                cur_italic = im.group(1) != '0'
            pos = end + 1
        else:
            # Find next override block or end
            next_block = text.find('{', pos)
            if next_block == -1:
                chunk = text[pos:]
                pos = len(text)
            else:
                chunk = text[pos:next_block]
                pos = next_block
            if chunk:
                segments.append(TextSegment(chunk, cur_bold, cur_italic))

    return segments if segments else [TextSegment("", default_bold, default_italic)]


def segments_to_ass(segments: list[TextSegment], default_bold: bool, default_italic: bool) -> str:
    """Convert TextSegments back to ASS text with minimal inline override blocks."""
    result: list[str] = []
    cur_bold = default_bold
    cur_italic = default_italic

    for seg in segments:
        tags: list[str] = []
        if seg.bold != cur_bold:
            tags.append(f"\\b{'1' if seg.bold else '0'}")
            cur_bold = seg.bold
        if seg.italic != cur_italic:
            tags.append(f"\\i{'1' if seg.italic else '0'}")
            cur_italic = seg.italic
        if tags:
            result.append("{" + "".join(tags) + "}")
        result.append(seg.text)

    return "".join(result)


def segments_to_html(segments: list[TextSegment]) -> str:
    """Convert TextSegments to HTML for QTextEdit."""
    parts: list[str] = []
    for seg in segments:
        text = seg.text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        # Convert \N to actual newlines for HTML
        text = text.replace("\\N", "<br>")
        if seg.bold and seg.italic:
            parts.append(f"<b><i>{text}</i></b>")
        elif seg.bold:
            parts.append(f"<b>{text}</b>")
        elif seg.italic:
            parts.append(f"<i>{text}</i>")
        else:
            parts.append(text)
    return "".join(parts)


def html_to_segments(html: str) -> list[TextSegment]:
    """Parse Qt HTML output into TextSegments.

    Qt's toHtml() produces a full document with <head>/<style> blocks.
    We skip everything outside <body> and inside <style>/<head> tags.
    """
    segments: list[TextSegment] = []
    bold_stack: list[bool] = [False]
    italic_stack: list[bool] = [False]
    skip_depth: int = 0  # > 0 means we're inside <head>/<style>, skip text
    p_count: int = 0  # track paragraph boundaries for \N insertion

    class Parser(HTMLParser):
        nonlocal skip_depth, p_count

        def handle_starttag(self, tag, attrs):
            nonlocal skip_depth, p_count
            if tag in ("head", "style"):
                skip_depth += 1
                return
            if skip_depth > 0:
                return
            attr_dict = dict(attrs)
            style = attr_dict.get("style", "")
            if tag == "p":
                # Each <p> after the first means a line break (Enter key)
                if p_count > 0:
                    segments.append(TextSegment("\\N", bold_stack[-1], italic_stack[-1]))
                p_count += 1
            elif tag in ("b", "strong"):
                bold_stack.append(True)
            elif tag in ("i", "em"):
                italic_stack.append(True)
            elif tag in ("span", "body"):
                # Qt uses inline styles like font-weight:700 and font-style:italic.
                # <body> carries the document-level font (from QTextEdit.setFont),
                # which is how Qt represents the editor's base bold/italic.
                is_bold = bold_stack[-1]
                is_italic = italic_stack[-1]
                if "font-weight:" in style:
                    weight_match = re.search(r"font-weight:\s*(\w+)", style)
                    if weight_match:
                        val = weight_match.group(1)
                        is_bold = val in ("bold", "700", "800", "900")
                if "font-style:" in style:
                    style_match = re.search(r"font-style:\s*(\w+)", style)
                    if style_match:
                        is_italic = style_match.group(1) == "italic"
                bold_stack.append(is_bold)
                italic_stack.append(is_italic)
            elif tag == "br":
                segments.append(TextSegment("\\N", bold_stack[-1], italic_stack[-1]))

        def handle_endtag(self, tag):
            nonlocal skip_depth
            if tag in ("head", "style"):
                skip_depth = max(0, skip_depth - 1)
                return
            if skip_depth > 0:
                return
            if tag in ("b", "strong") and len(bold_stack) > 1:
                bold_stack.pop()
            elif tag in ("i", "em") and len(italic_stack) > 1:
                italic_stack.pop()
            elif tag in ("span", "body"):
                if len(bold_stack) > 1:
                    bold_stack.pop()
                if len(italic_stack) > 1:
                    italic_stack.pop()

        def handle_data(self, data):
            if skip_depth > 0 or not data:
                return
            # Skip whitespace-only runs (inter-tag whitespace from Qt's HTML)
            if not data.strip():
                return
            segments.append(TextSegment(data, bold_stack[-1], italic_stack[-1]))

    parser = Parser()
    parser.feed(html)

    # Merge adjacent segments with same formatting
    if not segments:
        return [TextSegment("", False, False)]
    merged: list[TextSegment] = [segments[0]]
    for seg in segments[1:]:
        if seg.bold == merged[-1].bold and seg.italic == merged[-1].italic:
            merged[-1] = TextSegment(merged[-1].text + seg.text, seg.bold, seg.italic)
        else:
            merged.append(seg)
    return merged
