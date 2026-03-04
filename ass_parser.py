import re
from dataclasses import dataclass


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


_POS_RE = re.compile(r"\{[^}]*\\pos\((\d+(?:\.\d+)?),(\d+(?:\.\d+)?)\)[^}]*\}")
_POS_TAG_RE = re.compile(r"\\pos\([\d.]+,[\d.]+\)")
_FS_TAG_RE = re.compile(r"\\fs(\d+)")
_AN_TAG_RE = re.compile(r"\\an(\d)")
_OVERRIDE_BLOCK_RE = re.compile(r"\{[^}]*\}")


class AssFile:
    def __init__(self, path: str):
        self.path = path
        self.lines: list[str] = []
        self.play_res_x: int = 1920
        self.play_res_y: int = 1080
        self.label_font_name: str = "Arial"
        self.label_font_size: int = 36
        self.label_bold: bool = False
        self.label_italic: bool = False
        self.label_alignment: int = 2
        self.labels: list[LabelDialogue] = []
        self._parse(path)

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

        # Parse Label style from [V4+ Styles]
        for line in self.lines:
            stripped = line.strip()
            if stripped.startswith("Style:") and ",Label," in stripped or (
                stripped.startswith("Style: Label,")
                or stripped.startswith("Style:Label,")
            ):
                parts = stripped.split("Style:", 1)[1].split(",")
                name = parts[0].strip()
                if name == "Label":
                    self.label_font_name = parts[1].strip() if len(parts) > 1 else "Arial"
                    self.label_font_size = (
                        int(parts[2].strip()) if len(parts) > 2 else 36
                    )
                    # Bold is field index 7, Italic is field index 8
                    if len(parts) > 7:
                        self.label_bold = parts[7].strip() == "1"
                    if len(parts) > 8:
                        self.label_italic = parts[8].strip() == "1"
                    # Alignment is field index 18 in SSA v4+ style format
                    if len(parts) > 18:
                        try:
                            self.label_alignment = int(parts[18].strip())
                        except ValueError:
                            self.label_alignment = 2
                    break

        # Parse [Events] dialogues for Label style with \pos()
        # Format: Dialogue: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
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
            if style != "Label":
                continue
            start_str = parts[1].strip()
            end_str = parts[2].strip()
            text_field = parts[9]

            m = _POS_RE.search(text_field)
            if not m:
                continue

            pos_x = int(float(m.group(1)))
            pos_y = int(float(m.group(2)))
            # Extract display text: strip ALL override blocks
            display_text = _OVERRIDE_BLOCK_RE.sub("", text_field).strip()

            # Parse per-label \fs override
            fs_match = _FS_TAG_RE.search(text_field)
            font_size = int(fs_match.group(1)) if fs_match else None

            # Parse per-label \an override
            an_match = _AN_TAG_RE.search(text_field)
            alignment = int(an_match.group(1)) if an_match else None

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

    def set_label_text(self, label: LabelDialogue, new_text: str):
        """Update display text, preserving the override block."""
        label.text = new_text
        old_line = self.lines[label.line_index]
        # Split into the 10 dialogue fields
        stripped = old_line.strip()
        after = stripped.split("Dialogue:", 1)[1]
        parts = after.split(",", 9)
        if len(parts) < 10:
            return
        old_text_field = parts[9]
        # Extract override blocks from the old text field
        overrides = _OVERRIDE_BLOCK_RE.findall(old_text_field)
        override_prefix = "".join(overrides)
        # Rebuild: override block(s) + new display text
        parts[9] = override_prefix + new_text + "\n"
        new_after = ",".join(parts)
        self.lines[label.line_index] = "Dialogue:" + new_after

    def add_label(
        self,
        pos_x: int,
        pos_y: int,
        start_time: float,
        end_time: float,
        text: str,
        font_size: int | None = None,
        alignment: int | None = None,
    ) -> LabelDialogue:
        """Insert a new Dialogue line for a label. Returns the new LabelDialogue."""
        # Build the override block
        override = rf"{{\pos({pos_x},{pos_y})"
        if alignment is not None:
            override += rf"\an{alignment}"
        if font_size is not None:
            override += rf"\fs{font_size}"
        override += "}"

        start_str = _seconds_to_time(start_time)
        end_str = _seconds_to_time(end_time)
        line = f"Dialogue: 0,{start_str},{end_str},Label,,0,0,0,,{override}{text}\n"

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

        new_label = LabelDialogue(
            line_index=insert_idx,
            start_time=start_time,
            end_time=end_time,
            pos_x=pos_x,
            pos_y=pos_y,
            text=text,
            font_size=font_size,
            alignment=alignment,
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
        )

    def save(self, path: str | None = None):
        """Write the (possibly modified) .ass file back to disk."""
        out = path or self.path
        with open(out, "w", encoding="utf-8-sig") as f:
            f.writelines(self.lines)
