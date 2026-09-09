"""Every label change must light the unsaved indicators.

Property edits emit ``labels_mutated``; structural edits (delete, duplicate,
merge, split) emit ``labels_added`` / ``labels_removed`` and never
``labels_mutated``. All three have to reach the Save button's dot, the status
bar's modified chip, and the sidebar's per-row dirty set.

Follows the stand-in pattern of ``test_copy_style.py``: the real wiring and
the real slot bodies run; only the leaf widgets are faked.
"""

from pathlib import Path
from types import SimpleNamespace

from sub_label_pos.model.ass_file import AssFile
from sub_label_pos.model.label_store import LabelStore
from sub_label_pos.ui.controllers.label_edit_controller import LabelEditController

FIXTURE = Path(__file__).parent.parent / "fixtures" / "sample.ass"


class _SaveButton:
    def __init__(self):
        self.unsaved = False

    def set_unsaved(self, unsaved: bool) -> None:
        self.unsaved = unsaved


class _Chip:
    def __init__(self):
        self.visible = False

    def show(self) -> None:
        self.visible = True

    def hide(self) -> None:
        self.visible = False


class _Sidebar:
    def __init__(self):
        self.dirty_ids: set = set()

    def set_dirty_ids(self, ids: set) -> None:
        self.dirty_ids = set(ids)


def _window(qapp):
    """A MainWindow stand-in with its label-change signals really wired."""
    from sub_label_pos.ui.main_window import MainWindow

    store = LabelStore()
    store.load(AssFile.from_path(FIXTURE), source_path=FIXTURE)
    win = SimpleNamespace(
        _store=store,
        _save_btn=_SaveButton(),
        _sb_modified=_Chip(),
        _labels_sidebar=_Sidebar(),
        _dirty_label_ids=set(),
        _update_status_counts=lambda: None,
    )
    for name in ("_on_labels_changed", "_on_labels_removed", "_on_file_loaded_dirty"):
        slot = getattr(MainWindow, name)
        setattr(win, name, lambda *a, _s=slot, **kw: _s(win, *a, **kw))
    MainWindow._wire_label_change_signals(win)
    return win, store, LabelEditController(store)


def test_moving_a_label_flags_the_file_unsaved(qapp):
    win, store, ctrl = _window(qapp)
    lid = store.state.order[0]

    ctrl.move(lid, 5, 6)

    assert win._save_btn.unsaved is True
    assert win._sb_modified.visible is True
    assert win._dirty_label_ids == {lid}


def test_deleting_a_label_flags_the_file_unsaved(qapp):
    win, store, ctrl = _window(qapp)

    ctrl.delete({store.state.order[0]})

    assert win._save_btn.unsaved is True
    assert win._sb_modified.visible is True


def test_deleting_a_label_drops_its_dirty_id(qapp):
    win, store, ctrl = _window(qapp)
    doomed, survivor = store.state.order[0], store.state.order[1]
    ctrl.move(doomed, 5, 6)
    ctrl.move(survivor, 7, 8)

    ctrl.delete({doomed})

    assert win._dirty_label_ids == {survivor}
    assert win._labels_sidebar.dirty_ids == {survivor}


def test_duplicating_a_label_flags_the_new_label_dirty(qapp):
    win, store, ctrl = _window(qapp)

    new_id = ctrl.duplicate(store.state.order[0])

    assert win._save_btn.unsaved is True
    assert win._sb_modified.visible is True
    assert win._dirty_label_ids == {new_id}
