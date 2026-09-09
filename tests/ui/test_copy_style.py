"""Tests for the copy-style slot's argument handling.

``_on_copy_style`` is reachable from two places: the toolbar's
``copy_style_clicked(set)`` signal, which carries the checked attributes, and
the context-menu action, whose ``QAction.triggered`` signal carries a ``bool``.
The slot must copy everything when it is not handed a set of attributes.
"""

from types import SimpleNamespace

from sub_label_pos.model.ass_file import LabelDialogue


ALL_ATTRS = {"font_size", "alignment", "bold", "italic", "style",
             "primary_colour", "outline_colour", "outline_width", "rotation",
             "position"}


def _label() -> LabelDialogue:
    return LabelDialogue(
        line_index=0, start_time=0.0, end_time=1.0, pos_x=10, pos_y=20,
        text="hi", font_size=42, alignment=8, style_name="Label",
        bold=True, italic=True, primary_colour="&H00FFFFFF&",
        outline_colour="&H00000000&", outline_width=2.0, rotation=15.0,
        label_id="L0000-abc",
    )


def _window(label: LabelDialogue) -> SimpleNamespace:
    """A minimal stand-in for MainWindow carrying only what the slot reads."""
    return SimpleNamespace(
        _player=SimpleNamespace(selected_labels=lambda: [label]),
        _ass=SimpleNamespace(styles={}, label_font_size=36),
        _style_clipboard=None,
        statusBar=lambda: None,
    )


def test_copy_style_with_no_selection_copies_everything(qapp):
    """Called with no argument (all attributes) the clipboard is fully populated."""
    from sub_label_pos.ui.main_window import MainWindow

    win = _window(_label())
    MainWindow._on_copy_style(win)
    assert set(win._style_clipboard) == ALL_ATTRS
    assert all(v is not None for v in win._style_clipboard.values())


def test_copy_style_from_qaction_triggered_copies_everything(qapp):
    """QAction.triggered hands the slot its ``checked`` bool, not a set."""
    from sub_label_pos.ui.main_window import MainWindow

    win = _window(_label())
    MainWindow._on_copy_style(win, False)
    assert set(win._style_clipboard) == ALL_ATTRS
    assert all(v is not None for v in win._style_clipboard.values())


def test_copy_style_with_subset_copies_only_those_attributes(qapp):
    """An explicit attribute set leaves everything else out of the clipboard."""
    from sub_label_pos.ui.main_window import MainWindow

    win = _window(_label())
    MainWindow._on_copy_style(win, {"font_size", "rotation"})
    clip = win._style_clipboard
    assert clip["font_size"] == 42
    assert clip["rotation"] == 15.0
    assert all(clip[k] is None for k in ALL_ATTRS - {"font_size", "rotation"})
