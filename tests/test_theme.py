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
