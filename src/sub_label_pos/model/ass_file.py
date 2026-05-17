import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from sub_label_pos.model.text_segment import TextSegment  # noqa: F401  (re-exported)
from sub_label_pos.model.types import LabelId

if TYPE_CHECKING:
    from sub_label_pos.model.label_state import LabelState


@dataclass
class AssStyle:
    name: str
    font_name: str = "Arial"
    font_size: int = 36
    bold: bool = False
    italic: bool = False
    alignment: int = 2
    primary_colour: str = "&H00FFFFFF&"
    outline_colour: str = "&H00000000&"
    outline_width: float = 2.0
    raw_fields: list[str] = field(default_factory=list)


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
    primary_colour: str | None = None  # per-label \c override
    outline_colour: str | None = None  # per-label \3c override
    outline_width: float | None = None  # per-label \bord override
    rotation: float | None = None  # per-label \frz override in degrees
    rich_text: str = ""  # text with inline override blocks (leading block stripped)
    label_id: str = ""  # stable per-label id assigned at parse time


def _make_label_id(line_index: int, raw_line: str) -> LabelId:
    """Compute a stable per-label id from line index + raw line bytes."""
    h = hashlib.blake2b(raw_line.encode("utf-8"), digest_size=8).hexdigest()
    return LabelId(f"L{line_index:04d}-{h}")


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
_FS_TAG_RE = re.compile(r"\\fs(\d+)")
_AN_TAG_RE = re.compile(r"\\an(\d)")
_B_TAG_RE = re.compile(r"\\b(\d)")
_I_TAG_RE = re.compile(r"\\i(\d)")
_C_TAG_RE = re.compile(r"\\(?:1c|c)(&H[0-9A-Fa-f]+&)")
_3C_TAG_RE = re.compile(r"\\3c(&H[0-9A-Fa-f]+&)")
_BORD_TAG_RE = re.compile(r"\\bord([\d.]+)")
_FRZ_TAG_RE = re.compile(r"\\frz?(-?[\d.]+)")
_OVERRIDE_BLOCK_RE = re.compile(r"\{[^}]*\}")
_LEADING_BLOCK_RE = re.compile(r"^\{[^}]*\}")


# Rich-text (ASS<->segments<->HTML) conversions moved to geometry.rich_text.
# Re-exported here for backwards compatibility with existing callers.
from sub_label_pos.geometry.rich_text import (  # noqa: E402,F401
    parse_rich_text,
    segments_to_ass,
    segments_to_html,
    html_to_segments,
)


_V4_FORMAT_LINE = (
    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
    "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
    "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
    "Alignment, MarginL, MarginR, MarginV, Encoding\n"
)
_EVENTS_FORMAT_LINE = (
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
    "Effect, Text\n"
)


def _style_to_line(style: "AssStyle") -> str:
    """Render an AssStyle as a "Style:" line, preferring raw_fields when present."""
    if style.raw_fields:
        # raw_fields is the parsed split of an existing Style line.
        return "Style:" + ",".join(style.raw_fields) + "\n"
    # Synthesize a 23-field default Style line.
    fields = [
        f" {style.name}",
        style.font_name,
        str(style.font_size),
        style.primary_colour,
        "&H000000FF&",
        style.outline_colour,
        "&H00000000&",
        "1" if style.bold else "0",
        "1" if style.italic else "0",
        "0", "0",
        "100", "100", "0", "0",
        "1",
        f"{style.outline_width:g}",
        "0",
        str(style.alignment),
        "10", "10", "10", "1",
    ]
    return "Style:" + ",".join(fields) + "\n"


def _default_header_lines(
    styles: dict[str, "AssStyle"], play_res_x: int, play_res_y: int,
) -> list[str]:
    """Build a minimal Script Info + V4+ Styles header from a styles dict."""
    lines = [
        "[Script Info]\n",
        f"PlayResX: {play_res_x}\n",
        f"PlayResY: {play_res_y}\n",
        "\n",
        "[V4+ Styles]\n",
        _V4_FORMAT_LINE,
    ]
    for style in styles.values():
        lines.append(_style_to_line(style))
    lines.append("\n")
    return lines


def _ensure_events_header(header: list[str]) -> list[str]:
    """Ensure the header list ends with the [Events] + Format lines.

    If the supplied header already contains an [Events] section, return it
    untouched (a Format line will already be present in a well-formed file).
    Otherwise append the [Events] header so the caller can safely concat
    Dialogue lines after the returned list.
    """
    has_events = any(ln.strip().lower().startswith("[events]") for ln in header)
    if has_events:
        return list(header)
    out = list(header)
    if out and not out[-1].endswith("\n"):
        out[-1] = out[-1] + "\n"
    if not out or out[-1].strip() != "":
        out.append("\n")
    out.append("[Events]\n")
    out.append(_EVENTS_FORMAT_LINE)
    return out


def _dialogue_line_from_label(label: "LabelDialogue") -> str:
    """Render a LabelDialogue as a complete "Dialogue:" line (with trailing \\n).

    The leading override block is composed from the explicit fields on the
    LabelDialogue (always starting with \\pos), then ``label.rich_text`` is
    appended -- which already includes any inline override blocks that the
    user authored beyond the leading one. If ``rich_text`` itself starts
    with a leading override block (because the snapshot was just read from
    a freshly parsed AssFile and never normalized), that block is stripped
    first so we don't end up with two leading blocks on the saved line.
    """
    leading_parts: list[str] = [rf"\pos({label.pos_x},{label.pos_y})"]
    if label.alignment is not None:
        leading_parts.append(rf"\an{label.alignment}")
    if label.font_size is not None:
        leading_parts.append(rf"\fs{label.font_size}")
    if label.bold is not None:
        leading_parts.append(rf"\b{1 if label.bold else 0}")
    if label.italic is not None:
        leading_parts.append(rf"\i{1 if label.italic else 0}")
    if label.primary_colour is not None:
        leading_parts.append(rf"\c{label.primary_colour}")
    if label.outline_colour is not None:
        leading_parts.append(rf"\3c{label.outline_colour}")
    if label.outline_width is not None:
        leading_parts.append(rf"\bord{label.outline_width:g}")
    if label.rotation is not None:
        leading_parts.append(rf"\frz{label.rotation:g}")
    leading_block = "{" + "".join(leading_parts) + "}"

    # rich_text from the parser includes everything AFTER the original
    # leading block -- so it may contain inline blocks like {\b1}bold{\b0}
    # but should NOT start with a {...\pos...} block. Defensive strip in
    # case a snapshot still has its leading block attached.
    body = label.rich_text if label.rich_text else label.text
    leading_match = _LEADING_BLOCK_RE.match(body)
    if leading_match and r"\pos" in leading_match.group(0):
        body = body[leading_match.end():]

    start_str = _seconds_to_time(label.start_time)
    end_str = _seconds_to_time(label.end_time)
    style = label.style_name or "Default"
    return f"Dialogue: 0,{start_str},{end_str},{style},,0,0,0,,{leading_block}{body}\n"


class AssFile:
    def __init__(self, path: str | None = None):
        self.path = path
        self.lines: list[str] = []
        self.play_res_x: int = 1920
        self.play_res_y: int = 1080
        self.styles: dict[str, AssStyle] = {}
        self.labels: list[LabelDialogue] = []
        if path is not None:
            self._parse(path)

    @classmethod
    def from_path(cls, path: str | Path) -> "AssFile":
        """Construct an AssFile by reading bytes from disk."""
        return cls(str(path))

    @classmethod
    def from_bytes(cls, data: bytes) -> "AssFile":
        """Construct an AssFile by parsing bytes."""
        inst = cls.__new__(cls)
        inst.path = None
        inst.lines = []
        inst.play_res_x = 1920
        inst.play_res_y = 1080
        inst.styles = {}
        inst.labels = []
        inst._parse_text(data.decode("utf-8-sig"))
        return inst

    @classmethod
    def from_state(
        cls,
        state: "LabelState",
        header_lines: list[str] | None = None,
        play_res_x: int = 1920,
        play_res_y: int = 1080,
    ) -> "AssFile":
        """Build a fresh AssFile from a LabelState snapshot.

        Reconstructs ``self.lines`` by combining preserved ``header_lines``
        (everything before the [Events] section -- Script Info + V4+ Styles)
        with a freshly synthesized [Events] section built from
        ``state.order`` / ``state.labels`` / ``state.styles``.

        If ``header_lines`` is None, a minimal default header is generated
        from ``state.styles``.

        The styles dict on the resulting AssFile comes from ``state.styles``;
        the label list is rebuilt from each LabelDialogue's preserved
        ``rich_text`` plus a freshly composed leading override block.
        """
        inst = cls.__new__(cls)
        inst.path = None
        inst.play_res_x = play_res_x
        inst.play_res_y = play_res_y
        inst.styles = dict(state.styles)
        inst.labels = []

        # 1) Header (everything before [Events]).
        if header_lines is not None:
            header = [ln if ln.endswith("\n") else ln + "\n" for ln in header_lines]
        else:
            header = _default_header_lines(state.styles, play_res_x, play_res_y)

        # Detect play res from header if present (overrides the defaults).
        for ln in header:
            stripped = ln.strip()
            if stripped.startswith("PlayResX:"):
                try:
                    inst.play_res_x = int(stripped.split(":", 1)[1].strip())
                except ValueError:
                    pass
            elif stripped.startswith("PlayResY:"):
                try:
                    inst.play_res_y = int(stripped.split(":", 1)[1].strip())
                except ValueError:
                    pass

        # Ensure header ends with [Events] + Format line; if not, append.
        events_header = _ensure_events_header(header)

        lines: list[str] = list(events_header)

        # 2) Dialogue lines, one per label in state.order.
        for i, lid in enumerate(state.order):
            dlg = state.labels[lid]
            line = _dialogue_line_from_label(dlg)
            lines.append(line)
            # Mirror the dialogue onto a new LabelDialogue with line_index
            # pointing into the freshly built file.
            new_dlg = LabelDialogue(
                line_index=len(lines) - 1,
                start_time=dlg.start_time,
                end_time=dlg.end_time,
                pos_x=dlg.pos_x,
                pos_y=dlg.pos_y,
                text=dlg.text,
                font_size=dlg.font_size,
                alignment=dlg.alignment,
                style_name=dlg.style_name,
                bold=dlg.bold,
                italic=dlg.italic,
                primary_colour=dlg.primary_colour,
                outline_colour=dlg.outline_colour,
                outline_width=dlg.outline_width,
                rotation=dlg.rotation,
                rich_text=dlg.rich_text,
                label_id=dlg.label_id,
            )
            inst.labels.append(new_dlg)

        inst.lines = lines
        return inst

    def serialize(self) -> bytes:
        """Serialize back to ASS bytes (UTF-8 with BOM, matching save())."""
        return "".join(self.lines).encode("utf-8-sig")

    @property
    def events_start_index(self) -> int:
        """Index of the first line inside the [Events] section header.

        Returns the position of the ``[Events]`` line if present (so the
        slice ``self.lines[:events_start_index]`` is everything before
        [Events]; the user can pass it as ``header_lines`` to
        :meth:`from_state` to preserve Script Info + V4+ Styles intact).

        If no [Events] header is found, returns the index of the first
        Dialogue line. If neither exists, returns ``len(self.lines)``.
        """
        for i, line in enumerate(self.lines):
            if line.strip().lower().startswith("[events]"):
                return i
        for i, line in enumerate(self.lines):
            if line.strip().startswith("Dialogue:"):
                return i
        return len(self.lines)

    def label_by_id(self, label_id: LabelId | str) -> "LabelDialogue":
        """Look up a label by its LabelId."""
        for lbl in self.labels:
            if lbl.label_id == label_id:
                return lbl
        raise KeyError(label_id)

    def styles_by_name(self) -> dict[str, AssStyle]:
        """Return a dict mapping style name to AssStyle."""
        return dict(self.styles)

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
            text = f.read()
        self._parse_text(text)

    def _parse_text(self, text: str):
        self.lines = text.splitlines(keepends=True)

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
                primary_colour = parts[3].strip() if len(parts) > 3 else "&H00FFFFFF&"
                outline_colour = parts[5].strip() if len(parts) > 5 else "&H00000000&"
                outline_width = 2.0
                if len(parts) > 16:
                    try:
                        outline_width = float(parts[16].strip())
                    except ValueError:
                        outline_width = 2.0
                self.styles[name] = AssStyle(
                    name=name,
                    font_name=font_name,
                    font_size=font_size,
                    bold=bold,
                    italic=italic,
                    alignment=alignment,
                    primary_colour=primary_colour,
                    outline_colour=outline_colour,
                    outline_width=outline_width,
                    raw_fields=parts,
                )

        # Parse [Events] dialogues with \pos() whose style is in self.styles
        self.labels.clear()
        event_index = 0
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
            start_str = parts[1].strip()
            end_str = parts[2].strip()
            text_field = parts[9]

            m = _POS_RE.search(text_field)
            if not m:
                continue

            # Auto-create missing styles referenced by \pos() dialogues
            if style not in self.styles:
                self.styles[style] = AssStyle(name=style)

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

            c_match = _C_TAG_RE.search(leading_block)
            primary_colour = c_match.group(1) if c_match else None

            c3_match = _3C_TAG_RE.search(leading_block)
            outline_colour = c3_match.group(1) if c3_match else None

            bord_match = _BORD_TAG_RE.search(leading_block)
            outline_width = float(bord_match.group(1)) if bord_match else None

            frz_match = _FRZ_TAG_RE.search(leading_block)
            rotation = float(frz_match.group(1)) if frz_match else None

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
                    primary_colour=primary_colour,
                    outline_colour=outline_colour,
                    outline_width=outline_width,
                    rotation=rotation,
                    rich_text=rich_text,
                    label_id=_make_label_id(event_index, line),
                )
            )
            event_index += 1

    def set_label_style(self, label: LabelDialogue, style_name: str):
        """Change the style (field 3) in the raw dialogue line.

        Also removes the inline \\fs override so the new style's font size
        takes effect.
        """
        if style_name not in self.styles:
            return
        label.style_name = style_name
        label.font_size = None
        old_line = self.lines[label.line_index]
        # Remove inline \fs override so new style's font size applies
        old_line = _FS_TAG_RE.sub("", old_line)
        stripped = old_line.strip()
        after = stripped.split("Dialogue:", 1)[1]
        parts = after.split(",", 9)
        if len(parts) < 10:
            return
        parts[3] = style_name
        self.lines[label.line_index] = "Dialogue:" + ",".join(parts) + "\n"

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
        primary_colour: str | None = None,
        outline_colour: str | None = None,
        outline_width: float | None = None,
        rotation: float | None = None,
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
        if primary_colour is not None:
            override += rf"\c{primary_colour}"
        if outline_colour is not None:
            override += rf"\3c{outline_colour}"
        if outline_width is not None:
            override += rf"\bord{outline_width:g}"
        if rotation is not None:
            override += rf"\frz{rotation:g}"
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
            primary_colour=primary_colour,
            outline_colour=outline_colour,
            outline_width=outline_width,
            rotation=rotation,
            rich_text=rich_text if rich_text else text,
            label_id=_make_label_id(len(self.labels), line),
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
            primary_colour=label.primary_colour,
            outline_colour=label.outline_colour,
            outline_width=label.outline_width,
            rotation=label.rotation,
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

    def add_style(self, name: str, template_style: AssStyle | None = None) -> AssStyle:
        """Create a new style, optionally cloning a template. Returns the new AssStyle."""
        if template_style:
            new_fields = list(template_style.raw_fields)
        else:
            # Default 23-field ASS style
            new_fields = [
                name, "Arial", "36", "&H00FFFFFF&", "&H000000FF&",
                "&H00000000&", "&H00000000&", "0", "0", "0", "0",
                "100", "100", "0", "0", "1", "2", "0", "2",
                "10", "10", "10", "1",
            ]
        new_fields[0] = " " + name
        line_str = "Style:" + ",".join(new_fields) + "\n"

        # Insert after the last Style: line
        insert_idx = None
        for i, line in enumerate(self.lines):
            if line.strip().startswith("Style:"):
                insert_idx = i + 1
        if insert_idx is None:
            # No styles found, insert before first Dialogue or at end
            insert_idx = len(self.lines)
            for i, line in enumerate(self.lines):
                if line.strip().startswith("Dialogue:"):
                    insert_idx = i
                    break

        self.lines.insert(insert_idx, line_str)

        # Adjust line_index for all labels at or after insertion point
        for lb in self.labels:
            if lb.line_index >= insert_idx:
                lb.line_index += 1

        new_style = AssStyle(
            name=name,
            font_name=new_fields[1].strip(),
            font_size=int(new_fields[2].strip()),
            bold=new_fields[7].strip() == "1",
            italic=new_fields[8].strip() == "1",
            alignment=int(new_fields[18].strip()),
            primary_colour=new_fields[3].strip(),
            outline_colour=new_fields[5].strip(),
            outline_width=float(new_fields[16].strip()),
            raw_fields=new_fields,
        )
        self.styles[name] = new_style
        return new_style

    def update_style_field(self, style_name: str, field_index: int, value: str):
        """Update a specific field in a style's definition and raw line."""
        style = self.styles.get(style_name)
        if not style:
            return
        style.raw_fields[field_index] = value

        # Sync cached fields
        if field_index == 1:
            style.font_name = value.strip()
        elif field_index == 2:
            style.font_size = int(value.strip())
        elif field_index == 3:
            style.primary_colour = value.strip()
        elif field_index == 5:
            style.outline_colour = value.strip()
        elif field_index == 7:
            style.bold = value.strip() == "1"
        elif field_index == 8:
            style.italic = value.strip() == "1"
        elif field_index == 16:
            style.outline_width = float(value.strip())
        elif field_index == 18:
            style.alignment = int(value.strip())

        # Rebuild the Style line in self.lines
        for i, line in enumerate(self.lines):
            stripped = line.strip()
            if stripped.startswith("Style:"):
                parts = stripped.split("Style:", 1)[1].split(",")
                if parts[0].strip() == style_name:
                    self.lines[i] = "Style:" + ",".join(style.raw_fields) + "\n"
                    break

    def remove_inline_tag(self, label: LabelDialogue, tag_regex: 're.Pattern', attr_name: str):
        """Remove a specific inline tag from a label's leading override block."""
        old_line = self.lines[label.line_index]
        text_field = old_line.split(",", 9)[9] if old_line.strip().startswith("Dialogue:") else old_line
        leading_match = _LEADING_BLOCK_RE.search(text_field)
        if not leading_match:
            return
        block = leading_match.group(0)
        match = tag_regex.search(block)
        if not match:
            return
        # Remove the full match (tag + value) from the block
        new_block = block[:match.start()] + block[match.end():]
        new_line = old_line.replace(block, new_block, 1)
        self.lines[label.line_index] = new_line
        setattr(label, attr_name, None)

    def remove_inline_tag_from_all(self, style_name: str, tag_regex: 're.Pattern', attr_name: str):
        """Remove a specific inline tag from all labels using the given style."""
        for label in self.labels:
            if label.style_name == style_name:
                self.remove_inline_tag(label, tag_regex, attr_name)

    def save(self, path: str | None = None):
        """Write the (possibly modified) .ass file back to disk."""
        out = path or self.path
        with open(out, "w", encoding="utf-8-sig") as f:
            f.writelines(self.lines)
