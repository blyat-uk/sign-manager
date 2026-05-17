import inspect
from datetime import datetime, timedelta, timezone

import pytest

from sub_label_pos.ui import theme


def test_color_tokens_present():
    t = theme.Tokens
    assert t.accent == "#4a9eff"
    assert t.alert == "#ffcc55"
    assert t.bg_surface == "#1a1c20"
    assert t.text_emphasis == "#ffffff"


def test_spacing_tokens():
    assert theme.Tokens.sp_1 == 4
    assert theme.Tokens.sp_5 == 24


def test_radius_tokens():
    assert theme.Tokens.r_md == 6
    assert theme.Tokens.r_pill == 13


def _all_icon_factories() -> list[str]:
    return [
        name for name, member in inspect.getmembers(theme.Icons)
        if not name.startswith("_") and callable(member)
    ]


@pytest.mark.parametrize("icon_name", _all_icon_factories())
def test_icon_factory_produces_valid_icon(qapp, icon_name):
    """Every Icons.X() must return a non-null QIcon — guards against typos in
    Phosphor names and qtawesome prefix mistakes (e.g. `ph.bold.X` vs `ph.X-bold`).
    """
    factory = getattr(theme.Icons, icon_name)
    icon = factory()
    assert not icon.isNull(), f"Icons.{icon_name}() produced a null icon"


def test_format_relative_time():
    now = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
    f = theme.format_relative_time

    assert f(now - timedelta(minutes=5), now=now) == "5m ago"
    assert f(now - timedelta(minutes=58), now=now) == "58m ago"
    assert f(now - timedelta(hours=2), now=now) == "2h ago"
    assert f(now - timedelta(hours=20), now=now) == "20h ago"
    assert f(now - timedelta(hours=28), now=now) == "yesterday"
    assert f(now - timedelta(days=2), now=now) == "2d ago"
    assert f(now - timedelta(days=5), now=now) == "5d ago"
    assert f(now - timedelta(days=10), now=now) == "1 wk"
    assert f(now - timedelta(days=21), now=now) == "3 wks"
    assert f(now - timedelta(days=40), now=now) == "1 mo"
    assert f(now - timedelta(days=400), now=now) == "13 mo"


def test_format_relative_time_handles_none():
    assert theme.format_relative_time(None) == ""


def test_icon_button_constructs(qapp):
    btn = theme.IconButton(theme.Icons.save(), tooltip="Save (Ctrl+S)", icon_only=True)
    assert btn.toolTip() == "Save (Ctrl+S)"
    assert btn.size().width() == 30


def test_primary_button_unsaved_dot(qapp):
    btn = theme.PrimaryButton("Save", icon=theme.Icons.save())
    assert btn._unsaved is False
    btn.set_unsaved(True)
    assert btn._unsaved is True
    btn.set_unsaved(False)
    assert btn._unsaved is False


def test_stepper_emits_and_clamps(qapp):
    s = theme.Stepper(10, step=2, minimum=8, maximum=16)
    emitted = []
    s.value_changed.connect(emitted.append)
    s._on_plus(); s._on_plus(); s._on_plus(); s._on_plus()  # 10→12→14→16→clamp
    s._on_minus()  # 16→14
    assert emitted == [12, 14, 16, 14]


def test_segmented_toggle_exclusive(qapp):
    seg = theme.SegmentedToggle([
        ('L', theme.Icons.align_left(), 'L'),
        ('C', theme.Icons.align_center(), 'C'),
    ])
    seg.set_selected('L')
    assert seg._btns['L'].isChecked()
    assert not seg._btns['C'].isChecked()
    seg.set_selected('C')
    assert not seg._btns['L'].isChecked()
    assert seg._btns['C'].isChecked()
    seg.set_selected(None)
    assert not seg._btns['L'].isChecked()
    assert not seg._btns['C'].isChecked()


def test_color_swatch_mixed(qapp):
    from PyQt6.QtGui import QColor
    sw = theme.ColorSwatch()
    sw.set_color(QColor("#ff0000"))
    assert sw.color().name() == "#ff0000"
    sw.set_color(None)
    assert sw.color() is None


def test_status_chip_alert_variant(qapp):
    chip = theme.StatusChip(theme.Icons.files(), "3 / 12")
    assert chip._alert is False
    chip.set_alert(True)
    assert chip._alert is True


def test_style_chip_renames(qapp):
    chip = theme.StyleChip()
    chip.set_style_name("Caption")
    assert chip.text() == "Caption ▾"
