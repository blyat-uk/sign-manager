"""Tests for compute_label_rect — pure rect-computation logic."""

from dataclasses import replace
from pathlib import Path

import pytest
from PyQt6.QtCore import QPointF
from PyQt6.QtGui import QFont

from sign_manager.geometry.label_geometry import (
    compute_label_rect,
    libass_font_correction,
)
from sign_manager.model.ass_file import AssFile

FIXTURE = Path(__file__).parent.parent / "fixtures" / "sample.ass"


@pytest.fixture
def loaded_ass(qapp):
    return AssFile.from_path(FIXTURE)


def _make_font_provider(qapp):
    """Return a font_provider that ignores overrides and returns a fixed font."""
    font = QFont("Arial")
    font.setPixelSize(32)

    def provider(bold_override, italic_override):
        f = QFont(font)
        if bold_override is not None:
            f.setBold(bold_override)
        if italic_override is not None:
            f.setItalic(italic_override)
        return f
    return provider


def _identity_mapper(x, y):
    return QPointF(float(x), float(y))


# ── libass_font_correction ────────────────────────────────────────────────


def test_libass_correction_is_deterministic(qapp):
    """Multiple calls with the same font give the same correction factor."""
    font = QFont("Arial")
    font.setPixelSize(96)
    a = libass_font_correction(font)
    b = libass_font_correction(font)
    assert a == b


def test_libass_correction_returns_positive_finite(qapp):
    """The correction factor should be a positive finite number."""
    font = QFont("Arial")
    font.setPixelSize(96)
    c = libass_font_correction(font)
    assert c > 0
    # Reasonable bound — should be near 1.0 for normal fonts
    assert 0.1 < c < 10.0


# ── compute_label_rect ────────────────────────────────────────────────────


def test_compute_label_rect_returns_non_zero_rect(qapp, loaded_ass):
    """For a real label, the rect should have positive width and height."""
    label = loaded_ass.labels[0]
    style = loaded_ass.styles[label.style_name]
    rect = compute_label_rect(
        label,
        default_alignment=style.alignment,
        default_bold=style.bold,
        default_italic=style.italic,
        font_provider=_make_font_provider(qapp),
        ass_pos_to_widget=_identity_mapper,
    )
    assert rect.width() > 0
    assert rect.height() > 0


def test_compute_label_rect_position_changes_with_pos(qapp, loaded_ass):
    """Moving the label's pos_x should move the rect by the same amount."""
    label_a = loaded_ass.labels[0]
    label_b = replace(label_a, pos_x=label_a.pos_x + 100)
    style = loaded_ass.styles[label_a.style_name]
    fp = _make_font_provider(qapp)
    rect_a = compute_label_rect(
        label_a,
        default_alignment=style.alignment,
        default_bold=style.bold,
        default_italic=style.italic,
        font_provider=fp,
        ass_pos_to_widget=_identity_mapper,
    )
    rect_b = compute_label_rect(
        label_b,
        default_alignment=style.alignment,
        default_bold=style.bold,
        default_italic=style.italic,
        font_provider=fp,
        ass_pos_to_widget=_identity_mapper,
    )
    # Identity mapper => widget delta == ass delta == 100
    assert rect_b.x() - rect_a.x() == pytest.approx(100.0)
    # Width and height unchanged (same text, same font)
    assert rect_b.width() == pytest.approx(rect_a.width())
    assert rect_b.height() == pytest.approx(rect_a.height())


def test_compute_label_rect_alignment_anchors_correctly(qapp, loaded_ass):
    """Alignment 1 (bottom-left): the label.pos is the bottom-left corner.
    Alignment 5 (middle-center): the label.pos is the rect center.
    Alignment 9 (top-right): the label.pos is the top-right corner.
    """
    label = loaded_ass.labels[0]
    style = loaded_ass.styles[label.style_name]
    fp = _make_font_provider(qapp)

    common = dict(
        default_alignment=style.alignment,
        default_bold=style.bold,
        default_italic=style.italic,
        font_provider=fp,
        ass_pos_to_widget=_identity_mapper,
    )

    pos = QPointF(float(label.pos_x), float(label.pos_y))

    rect1 = compute_label_rect(replace(label, alignment=1), **common)
    rect5 = compute_label_rect(replace(label, alignment=5), **common)
    rect9 = compute_label_rect(replace(label, alignment=9), **common)

    # Alignment 1: bottom-left at pos
    assert rect1.x() == pytest.approx(pos.x())
    assert rect1.bottom() == pytest.approx(pos.y())

    # Alignment 5: center at pos
    assert rect5.center().x() == pytest.approx(pos.x())
    assert rect5.center().y() == pytest.approx(pos.y())

    # Alignment 9: top-right at pos
    assert rect9.right() == pytest.approx(pos.x())
    assert rect9.top() == pytest.approx(pos.y())


def test_compute_label_rect_uses_default_alignment_when_label_alignment_none(
    qapp, loaded_ass,
):
    """If label.alignment is None, the passed default_alignment is used."""
    label = loaded_ass.labels[0]
    label = replace(label, alignment=None)
    style = loaded_ass.styles[label.style_name]
    fp = _make_font_provider(qapp)

    pos = QPointF(float(label.pos_x), float(label.pos_y))

    rect_via_default = compute_label_rect(
        label,
        default_alignment=1,
        default_bold=style.bold,
        default_italic=style.italic,
        font_provider=fp,
        ass_pos_to_widget=_identity_mapper,
    )
    # Alignment 1 = bottom-left anchor
    assert rect_via_default.x() == pytest.approx(pos.x())
    assert rect_via_default.bottom() == pytest.approx(pos.y())
