"""A single rich-text run with bold/italic flags.

Lives in its own module so pure conversion code (``geometry.rich_text``)
can import the dataclass without pulling in the entire ASS parser.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TextSegment:
    text: str
    bold: bool
    italic: bool
