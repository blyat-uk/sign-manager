"""LabelsSidebar must refresh on structural label changes.

Deleting or adding labels emits ``labels_removed`` / ``labels_added`` — not
``labels_mutated`` — so the sidebar needs its own subscriptions or the row
list keeps showing labels that no longer exist.
"""

from pathlib import Path

from sub_label_pos.model.ass_file import AssFile
from sub_label_pos.model.label_store import LabelStore
from sub_label_pos.ui.controllers.label_edit_controller import LabelEditController
from sub_label_pos.ui.labels_sidebar import LabelsSidebar

FIXTURE = Path(__file__).parent.parent / "fixtures" / "sample.ass"


def _make_sidebar(qapp):
    """Build a visible sidebar wired to a store loaded from the fixture."""
    ass = AssFile(str(FIXTURE))
    store = LabelStore()
    store.load(ass, FIXTURE)
    ctrl = LabelEditController(store)
    sidebar = LabelsSidebar(store)
    sidebar.show()  # rows only rebuild while the widget is visible
    return sidebar, store, ctrl


def _listed_texts(sidebar) -> list[str]:
    return [lb.text for row in sidebar._rows for lb in row.labels]


def test_deleting_all_labels_clears_the_rows(qapp):
    sidebar, store, ctrl = _make_sidebar(qapp)
    assert _listed_texts(sidebar) == ["Hello", "World"]

    ctrl.delete(set(store.state.order))

    assert _listed_texts(sidebar) == []
    assert sidebar._list.count() == 0


def test_deleting_one_label_leaves_the_others(qapp):
    sidebar, store, ctrl = _make_sidebar(qapp)
    first = store.state.order[0]

    ctrl.delete({first})

    assert _listed_texts(sidebar) == ["World"]


def test_duplicating_a_label_adds_it_to_the_rows(qapp):
    sidebar, store, ctrl = _make_sidebar(qapp)

    ctrl.duplicate(store.state.order[0])

    assert _listed_texts(sidebar) == ["Hello", "Hello", "World"]


def _row_dirty_flags(sidebar) -> list[bool]:
    return [
        sidebar._list.itemWidget(sidebar._list.item(i))._dirty
        for i in range(sidebar._list.count())
    ]


def test_set_dirty_ids_lights_the_row_dot_immediately(qapp):
    sidebar, store, ctrl = _make_sidebar(qapp)
    assert _row_dirty_flags(sidebar) == [False]

    sidebar.set_dirty_ids({store.state.order[0]})

    assert _row_dirty_flags(sidebar) == [True]
