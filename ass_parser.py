import re
from dataclasses import dataclass, field


@dataclass
class AssStyle:
    name: str
    font_name: str = "Arial"
    font_size: int = 36
    bold: bool = False
    italic: bool = False
    alignment: int = 2
    raw_fields: list[str] = field(default_factory=list)


@dataclass
class TextSegment:
    text: str
    bold: bool
    italic: bool


@dataclass
class LabelDialogue:
    line_index: int
    start_time: float
    end_time: float
    pos_x: int
    pos_y: int
    text: str
    font_size: int | None = None  # per-label \fs override; None = style default
    alignment: int | None = None  # per-label \an override; None = style default
    style_name: str = "Label"
    bold: bool | None = None  # per-label \b override in leading block
    italic: bool | None = None  # per-label \i override in leading block
    rich_text: str = ""  # text with inline override blocks (leading block stripped)


def _time_to_seconds(t: str) -> float:
    """Parse ASS time format H:MM:SS.CC to float seconds."""
    h, m, rest = t.split(":")
    s, cs = rest.split(".")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(cs) / 100.0


def _seconds_to_time(sec: float) -> str:
    """Convert float seconds to ASS time format H:MM:SS.CC."""
    h = int(sec // 3600)
    sec %= 3600
    m = int(sec // 60)
    sec %= 60
    s = int(sec)
    cs = int(round((sec - s) * 100))
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


_POS_RE = re.compile(r"\{[^}]*\\pos\((-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)\)[^}]*\}")
_POS_TAG_RE = re.compile(r"\\pos\(-?[\d.]+,-?[\d.]+\)")
_FS_TAG_RE = re.compile(r"\\fs(\d+)")
_AN_TAG_RE = re.compile(r"\\an(\d)")
_B_TAG_RE = re.compile(r"\\b(\d)")
_I_TAG_RE = re.compile(r"\\i(\d)")
_OVERRIDE_BLOCK_RE = re.compile(r"\{[^}]*\}")
_LEADING_BLOCK_RE = re.compile(r"^\{[^}]*\}")


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
    from html.parser import HTMLParser

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
            elif tag == "span":
                # Qt uses inline styles like font-weight:700 and font-style:italic
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
            elif tag == "span":
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


class AssFile:
    def __init__(self, path: str):
        self.path = path
        self.lines: list[str] = []
        self.play_res_x: int = 1920
        self.play_res_y: int = 1080
        self.styles: dict[str, AssStyle] = {}
        self.labels: list[LabelDialogue] = []
        self._parse(path)

    @property
    def label_font_name(self) -> str:
        s = self.styles.get("Label")
        return s.font_name if s else (next(iter(self.styles.values())).font_name if self.styles else "Arial")

    @property
    def label_font_size(self) -> int:
        s = self.styles.get("Label")
        return s.font_size if s else (next(iter(self.styles.values())).font_size if self.styles else 36)

    @property
    def label_bold(self) -> bool:
        s = self.styles.get("Label")
        return s.bold if s else (next(iter(self.styles.values())).bold if self.styles else False)

    @property
    def label_italic(self) -> bool:
        s = self.styles.get("Label")
        return s.italic if s else (next(iter(self.styles.values())).italic if self.styles else False)

    @property
    def label_alignment(self) -> int:
        s = self.styles.get("Label")
        return s.alignment if s else (next(iter(self.styles.values())).alignment if self.styles else 2)

    def _parse(self, path: str):
        with open(path, "r", encoding="utf-8-sig") as f:
            self.lines = f.read().splitlines(keepends=True)

        # If the file didn't have line endings, add them back
        self.lines = [
            line if line.endswith("\n") else line + "\n" for line in self.lines
        ]

        for line in self.lines:
            stripped = line.strip()
            if stripped.startswith("PlayResX:"):
                self.play_res_x = int(stripped.split(":", 1)[1].strip())
            elif stripped.startswith("PlayResY:"):
                self.play_res_y = int(stripped.split(":", 1)[1].strip())

        # Parse all styles from [V4+ Styles]
        self.styles.clear()
        for line in self.lines:
            stripped = line.strip()
            if stripped.startswith("Style:"):
                parts = stripped.split("Style:", 1)[1].split(",")
                name = parts[0].strip()
                font_name = parts[1].strip() if len(parts) > 1 else "Arial"
                font_size = int(parts[2].strip()) if len(parts) > 2 else 36
                bold = parts[7].strip() == "1" if len(parts) > 7 else False
                italic = parts[8].strip() == "1" if len(parts) > 8 else False
                alignment = 2
                if len(parts) > 18:
                    try:
                        alignment = int(parts[18].strip())
                    except ValueError:
                        alignment = 2
                self.styles[name] = AssStyle(
                    name=name,
                    font_name=font_name,
                    font_size=font_size,
                    bold=bold,
                    italic=italic,
                    alignment=alignment,
                    raw_fields=parts,
                )

        # Parse [Events] dialogues with \pos() whose style is in self.styles
        self.labels.clear()
        for i, line in enumerate(self.lines):
            stripped = line.strip()
            if not stripped.startswith("Dialogue:"):
                continue
            after = stripped.split("Dialogue:", 1)[1]
            # Split on first 9 commas to get the fields
            parts = after.split(",", 9)
            if len(parts) < 10:
                continue
            style = parts[3].strip()
            if style not in self.styles:
                continue
            start_str = parts[1].strip()
            end_str = parts[2].strip()
            text_field = parts[9]

            m = _POS_RE.search(text_field)
            if not m:
                continue

            pos_x = int(float(m.group(1)))
            pos_y = int(float(m.group(2)))

            # Extract the leading override block (the one with \pos)
            leading_match = _LEADING_BLOCK_RE.search(text_field)
            leading_block = leading_match.group(0) if leading_match else ""

            # rich_text = everything after the leading block
            rich_text = text_field[len(leading_block):].strip() if leading_block else text_field.strip()

            # Display text: strip ALL override blocks from rich_text
            display_text = _OVERRIDE_BLOCK_RE.sub("", rich_text).strip()

            # Parse per-label overrides from leading block only
            fs_match = _FS_TAG_RE.search(leading_block)
            font_size = int(fs_match.group(1)) if fs_match else None

            an_match = _AN_TAG_RE.search(leading_block)
            alignment = int(an_match.group(1)) if an_match else None

            b_match = _B_TAG_RE.search(leading_block)
            bold = (b_match.group(1) != '0') if b_match else None

            i_match = _I_TAG_RE.search(leading_block)
            italic = (i_match.group(1) != '0') if i_match else None

            self.labels.append(
                LabelDialogue(
                    line_index=i,
                    start_time=_time_to_seconds(start_str),
                    end_time=_time_to_seconds(end_str),
                    pos_x=pos_x,
                    pos_y=pos_y,
                    text=display_text,
                    font_size=font_size,
                    alignment=alignment,
                    style_name=style,
                    bold=bold,
                    italic=italic,
                    rich_text=rich_text,
                )
            )

    def set_label_position(self, label: LabelDialogue, new_x: int, new_y: int):
        """Update a label's position in-place, preserving other override tags."""
        label.pos_x = new_x
        label.pos_y = new_y
        old_line = self.lines[label.line_index]
        # Surgically replace only the \pos() tag, preserving \fs and other tags
        new_line = _POS_TAG_RE.sub(
            rf"\\pos({new_x},{new_y})", old_line, count=1
        )
        self.lines[label.line_index] = new_line

    def set_label_font_size(self, label: LabelDialogue, size: int):
        """Set/update \\fs in the override block for a label."""
        label.font_size = size
        old_line = self.lines[label.line_index]
        if _FS_TAG_RE.search(old_line):
            # Replace existing \fs tag
            new_line = _FS_TAG_RE.sub(rf"\\fs{size}", old_line, count=1)
        else:
            # Insert \fs right after \pos(...) inside the override block
            new_line = _POS_TAG_RE.sub(
                lambda m: m.group(0) + rf"\fs{size}", old_line, count=1
            )
        self.lines[label.line_index] = new_line

    def set_label_alignment(self, label: LabelDialogue, alignment: int):
        """Set/update \\an in the override block for a label."""
        label.alignment = alignment
        old_line = self.lines[label.line_index]
        if _AN_TAG_RE.search(old_line):
            new_line = _AN_TAG_RE.sub(rf"\\an{alignment}", old_line, count=1)
        else:
            # Insert \an right after \pos(...) inside the override block
            new_line = _POS_TAG_RE.sub(
                lambda m: m.group(0) + rf"\an{alignment}", old_line, count=1
            )
        self.lines[label.line_index] = new_line

    def set_label_bold(self, label: LabelDialogue, bold: bool):
        """Set/update \\b in the leading override block for a label."""
        label.bold = bold
        old_line = self.lines[label.line_index]
        tag = rf"\b{1 if bold else 0}"
        # Check if there's already a \b tag in the leading block
        leading_match = _LEADING_BLOCK_RE.search(old_line.split(",", 9)[9] if old_line.strip().startswith("Dialogue:") else old_line)
        if leading_match:
            block = leading_match.group(0)
            b_match = _B_TAG_RE.search(block)
            if b_match:
                new_block = block[:b_match.start()] + tag + block[b_match.end():]
                new_line = old_line.replace(block, new_block, 1)
            else:
                # Insert after \pos(...)
                new_line = _POS_TAG_RE.sub(
                    lambda m: m.group(0) + tag, old_line, count=1
                )
            self.lines[label.line_index] = new_line

    def set_label_italic(self, label: LabelDialogue, italic: bool):
        """Set/update \\i in the leading override block for a label."""
        label.italic = italic
        old_line = self.lines[label.line_index]
        tag = rf"\i{1 if italic else 0}"
        leading_match = _LEADING_BLOCK_RE.search(old_line.split(",", 9)[9] if old_line.strip().startswith("Dialogue:") else old_line)
        if leading_match:
            block = leading_match.group(0)
            i_match = _I_TAG_RE.search(block)
            if i_match:
                new_block = block[:i_match.start()] + tag + block[i_match.end():]
                new_line = old_line.replace(block, new_block, 1)
            else:
                new_line = _POS_TAG_RE.sub(
                    lambda m: m.group(0) + tag, old_line, count=1
                )
            self.lines[label.line_index] = new_line

    def set_label_style(self, label: LabelDialogue, style_name: str):
        """Change the style (field 3) in the raw dialogue line."""
        if style_name not in self.styles:
            return
        label.style_name = style_name
        old_line = self.lines[label.line_index]
        stripped = old_line.strip()
        after = stripped.split("Dialogue:", 1)[1]
        parts = after.split(",", 9)
        if len(parts) < 10:
            return
        parts[3] = style_name
        self.lines[label.line_index] = "Dialogue:" + ",".join(parts)

    def set_label_text(self, label: LabelDialogue, new_text: str):
        """Update display text, preserving the override block."""
        label.text = new_text
        label.rich_text = new_text
        old_line = self.lines[label.line_index]
        # Split into the 10 dialogue fields
        stripped = old_line.strip()
        after = stripped.split("Dialogue:", 1)[1]
        parts = after.split(",", 9)
        if len(parts) < 10:
            return
        old_text_field = parts[9]
        # Extract the leading override block
        leading_match = _LEADING_BLOCK_RE.search(old_text_field)
        leading_block = leading_match.group(0) if leading_match else ""
        # Rebuild: leading block + new display text
        parts[9] = leading_block + new_text + "\n"
        new_after = ",".join(parts)
        self.lines[label.line_index] = "Dialogue:" + new_after

    def set_label_rich_text(self, label: LabelDialogue, rich_text: str):
        """Update text content with rich formatting (inline override blocks)."""
        label.rich_text = rich_text
        label.text = _OVERRIDE_BLOCK_RE.sub("", rich_text).strip()
        old_line = self.lines[label.line_index]
        stripped = old_line.strip()
        after = stripped.split("Dialogue:", 1)[1]
        parts = after.split(",", 9)
        if len(parts) < 10:
            return
        old_text_field = parts[9]
        leading_match = _LEADING_BLOCK_RE.search(old_text_field)
        leading_block = leading_match.group(0) if leading_match else ""
        parts[9] = leading_block + rich_text + "\n"
        self.lines[label.line_index] = "Dialogue:" + ",".join(parts)

    def add_label(
        self,
        pos_x: int,
        pos_y: int,
        start_time: float,
        end_time: float,
        text: str,
        font_size: int | None = None,
        alignment: int | None = None,
        style_name: str = "Label",
        bold: bool | None = None,
        italic: bool | None = None,
        rich_text: str = "",
    ) -> LabelDialogue:
        """Insert a new Dialogue line for a label. Returns the new LabelDialogue."""
        # Build the override block
        override = rf"{{\pos({pos_x},{pos_y})"
        if alignment is not None:
            override += rf"\an{alignment}"
        if font_size is not None:
            override += rf"\fs{font_size}"
        if bold is not None:
            override += rf"\b{1 if bold else 0}"
        if italic is not None:
            override += rf"\i{1 if italic else 0}"
        override += "}"

        # Use the actual style name, falling back to first available style
        actual_style = style_name
        if actual_style not in self.styles and self.styles:
            actual_style = next(iter(self.styles))

        content = rich_text if rich_text else text
        start_str = _seconds_to_time(start_time)
        end_str = _seconds_to_time(end_time)
        line = f"Dialogue: 0,{start_str},{end_str},{actual_style},,0,0,0,,{override}{content}\n"

        # Find insertion point: after last Dialogue line, or at end
        insert_idx = len(self.lines)
        for i in range(len(self.lines) - 1, -1, -1):
            if self.lines[i].strip().startswith("Dialogue:"):
                insert_idx = i + 1
                break

        self.lines.insert(insert_idx, line)

        # Adjust line_index for existing labels at or after insertion point
        for lb in self.labels:
            if lb.line_index >= insert_idx:
                lb.line_index += 1

        display_text = _OVERRIDE_BLOCK_RE.sub("", rich_text).strip() if rich_text else text
        new_label = LabelDialogue(
            line_index=insert_idx,
            start_time=start_time,
            end_time=end_time,
            pos_x=pos_x,
            pos_y=pos_y,
            text=display_text,
            font_size=font_size,
            alignment=alignment,
            style_name=actual_style,
            bold=bold,
            italic=italic,
            rich_text=rich_text if rich_text else text,
        )
        self.labels.append(new_label)
        return new_label

    def delete_label(self, label: LabelDialogue):
        """Remove a label's Dialogue line and adjust indices."""
        idx = label.line_index
        del self.lines[idx]
        self.labels.remove(label)
        # Adjust line_index for labels after the deleted line
        for lb in self.labels:
            if lb.line_index > idx:
                lb.line_index -= 1

    def delete_labels(self, labels: list[LabelDialogue]):
        """Delete multiple labels, processing in reverse line_index order."""
        for label in sorted(labels, key=lambda lb: lb.line_index, reverse=True):
            self.delete_label(label)

    def duplicate_label(
        self, label: LabelDialogue, offset_x: int = 30, offset_y: int = 30
    ) -> LabelDialogue:
        """Clone a label at a slight offset."""
        return self.add_label(
            pos_x=label.pos_x + offset_x,
            pos_y=label.pos_y + offset_y,
            start_time=label.start_time,
            end_time=label.end_time,
            text=label.text,
            font_size=label.font_size,
            alignment=label.alignment,
            style_name=label.style_name,
            bold=label.bold,
            italic=label.italic,
            rich_text=label.rich_text,
        )

    def merge_labels(
        self,
        labels: list[LabelDialogue],
        order: list[int],
        separator: str,
    ) -> LabelDialogue:
        """Merge multiple labels into one. Returns the new label."""
        anchor = min(labels, key=lambda lb: (lb.start_time, lb.line_index))
        merged_text = separator.join(labels[i].text for i in order)
        start_time = min(lb.start_time for lb in labels)
        end_time = max(lb.end_time for lb in labels)
        self.delete_labels(labels)
        return self.add_label(
            pos_x=anchor.pos_x,
            pos_y=anchor.pos_y,
            start_time=start_time,
            end_time=end_time,
            text=merged_text,
            font_size=anchor.font_size,
            alignment=anchor.alignment,
            style_name=anchor.style_name,
            bold=anchor.bold,
            italic=anchor.italic,
        )

    def save(self, path: str | None = None):
        """Write the (possibly modified) .ass file back to disk."""
        out = path or self.path
        with open(out, "w", encoding="utf-8-sig") as f:
            f.writelines(self.lines)
