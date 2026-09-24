"""Tests for ASS<->widget coordinate mapping."""

import pytest
from PyQt6.QtCore import QPointF

from sign_manager.geometry.coords import ass_to_widget, widget_to_ass, scale_factor


def test_scale_uniform_to_matched_aspect():
    """1920x1080 ASS into 960x540 widget (same aspect) gives 0.5."""
    assert scale_factor((1920, 1080), (960, 540)) == pytest.approx(0.5)


def test_scale_fits_to_shorter_axis_when_widget_wider():
    """Widget wider than ASS aspect: scale by height (the limiting axis)."""
    assert scale_factor((1920, 1080), (4000, 540)) == pytest.approx(0.5)


def test_scale_fits_to_shorter_axis_when_widget_taller():
    assert scale_factor((1920, 1080), (960, 4000)) == pytest.approx(0.5)


def test_ass_to_widget_scales():
    p = ass_to_widget(QPointF(100, 200), (1920, 1080), (960, 540))
    assert p.x() == pytest.approx(50.0)
    assert p.y() == pytest.approx(100.0)


def test_widget_to_ass_scales():
    p = widget_to_ass(QPointF(50, 100), (1920, 1080), (960, 540))
    assert p.x() == pytest.approx(100.0)
    assert p.y() == pytest.approx(200.0)


def test_roundtrip_identity():
    ass = (1920, 1080)
    widget = (1234, 700)
    p = QPointF(123.5, 456.5)
    p2 = widget_to_ass(ass_to_widget(p, ass, widget), ass, widget)
    assert p2.x() == pytest.approx(p.x())
    assert p2.y() == pytest.approx(p.y())


def test_zero_scale_returns_zero():
    """Edge: widget size 0 should not crash."""
    p = ass_to_widget(QPointF(100, 100), (1920, 1080), (0, 0))
    assert p.x() == 0.0
    assert p.y() == 0.0
    p = widget_to_ass(QPointF(100, 100), (1920, 1080), (0, 0))
    assert p.x() == 0.0
    assert p.y() == 0.0
