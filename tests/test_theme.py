import inspect

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
