def test_pytest_works():
    assert True


def test_retime_bar_constructs_hidden_with_empty_selection(qapp):
    from sub_label_pos.model.ass_file import AssFile
    from sub_label_pos.model.label_store import LabelStore
    from sub_label_pos.ui.controllers.label_edit_controller import LabelEditController
    from sub_label_pos.ui.controllers.retime_controller import RetimeController
    from sub_label_pos.ui.retime_bar import RetimeBar
    from pathlib import Path

    fixture = Path(__file__).parent / "fixtures" / "sample.ass"
    ass = AssFile(str(fixture))
    store = LabelStore()
    store.load(ass, fixture)
    edit = LabelEditController(store)
    rt = RetimeController(
        store, edit, fps_provider=lambda: 30.0, current_time_provider=lambda: 0.0,
    )
    bar = RetimeBar(store, rt)
    assert bar.isHidden() is True


def test_retime_bar_becomes_visible_on_selection(qapp):
    from sub_label_pos.model.ass_file import AssFile
    from sub_label_pos.model.label_store import LabelStore
    from sub_label_pos.ui.controllers.label_edit_controller import LabelEditController
    from sub_label_pos.ui.controllers.retime_controller import RetimeController
    from sub_label_pos.ui.retime_bar import RetimeBar
    from pathlib import Path

    fixture = Path(__file__).parent / "fixtures" / "sample.ass"
    ass = AssFile(str(fixture))
    store = LabelStore()
    store.load(ass, fixture)
    edit = LabelEditController(store)
    rt = RetimeController(
        store, edit, fps_provider=lambda: 30.0, current_time_provider=lambda: 0.0,
    )
    bar = RetimeBar(store, rt)
    lid = next(iter(store.state.labels.keys()))
    store.set_selection({lid})
    # isHidden returns False once selection arrives — explicit visibility flag check
    assert bar.isHidden() is False


def test_focused_timeline_constructs_hidden_with_empty_selection(qapp):
    from sub_label_pos.model.ass_file import AssFile
    from sub_label_pos.model.label_store import LabelStore
    from sub_label_pos.ui.controllers.label_edit_controller import LabelEditController
    from sub_label_pos.ui.controllers.retime_controller import RetimeController
    from sub_label_pos.ui.focused_timeline import FocusedTimeline
    from pathlib import Path

    fixture = Path(__file__).parent / "fixtures" / "sample.ass"
    ass = AssFile(str(fixture))
    store = LabelStore()
    store.load(ass, fixture)
    edit = LabelEditController(store)
    rt = RetimeController(
        store, edit, fps_provider=lambda: 30.0, current_time_provider=lambda: 0.0,
    )
    ft = FocusedTimeline(store, rt, fps_provider=lambda: 30.0)
    assert ft.isHidden() is True


def test_focused_timeline_fits_to_selection_when_selected(qapp):
    from sub_label_pos.model.ass_file import AssFile
    from sub_label_pos.model.label_store import LabelStore
    from sub_label_pos.ui.controllers.label_edit_controller import LabelEditController
    from sub_label_pos.ui.controllers.retime_controller import RetimeController
    from sub_label_pos.ui.focused_timeline import FocusedTimeline
    from pathlib import Path

    fixture = Path(__file__).parent / "fixtures" / "sample.ass"
    ass = AssFile(str(fixture))
    store = LabelStore()
    store.load(ass, fixture)
    edit = LabelEditController(store)
    rt = RetimeController(
        store, edit, fps_provider=lambda: 30.0, current_time_provider=lambda: 0.0,
    )
    ft = FocusedTimeline(store, rt, fps_provider=lambda: 30.0)
    lid = next(iter(store.state.labels.keys()))
    store.set_selection({lid})
    vs, ve = ft.view_window()
    label = store.state.labels[lid]
    # The view should contain the selected label.
    assert vs <= label.start_time
    assert ve >= label.end_time
    # And be at least the minimum view span.
    assert (ve - vs) >= 4.0
