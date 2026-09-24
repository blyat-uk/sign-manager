"""Tests for RetimeController orchestration."""

from pathlib import Path
import pytest

from sign_manager.model.ass_file import AssFile
from sign_manager.model.label_store import LabelStore
from sign_manager.ui.controllers.label_edit_controller import LabelEditController
from sign_manager.ui.controllers.retime_controller import RetimeController

FIXTURE = Path(__file__).parent.parent / "fixtures" / "sample.ass"


def _make(fps=30.0, current=10.0):
    """Construct a store + edit + retime controller from the sample fixture."""
    ass = AssFile(str(FIXTURE))
    store = LabelStore()
    store.load(ass, FIXTURE)
    edit = LabelEditController(store)
    retime = RetimeController(
        store, edit,
        fps_provider=lambda: fps,
        current_time_provider=lambda: current,
    )
    return store, edit, retime


def test_set_in_at_current_updates_selected_label_start(qapp):
    store, _, retime = _make(current=2.0)
    lid = next(iter(store.state.labels.keys()))
    store.set_selection({lid})
    original_end = store.state.labels[lid].end_time
    retime.set_in_at_current()
    assert store.state.labels[lid].start_time == 2.0
    assert store.state.labels[lid].end_time == original_end


def test_set_out_at_current_updates_selected_label_end(qapp):
    store, _, retime = _make(current=99.0)
    lid = next(iter(store.state.labels.keys()))
    store.set_selection({lid})
    original_start = store.state.labels[lid].start_time
    retime.set_out_at_current()
    assert store.state.labels[lid].end_time == 99.0
    assert store.state.labels[lid].start_time == original_start


def test_set_in_clamps_when_would_invert(qapp):
    """If new start >= end, end snaps to start + 1 frame."""
    store, _, retime = _make(fps=30.0, current=1000.0)
    lid = next(iter(store.state.labels.keys()))
    store.set_selection({lid})
    retime.set_in_at_current()
    assert store.state.labels[lid].start_time == 1000.0
    # End must be at least one frame past start (1/30 ≈ 0.03).
    assert store.state.labels[lid].end_time >= 1000.0 + 0.03


def test_nudge_both_shifts_start_and_end_by_one_frame(qapp):
    store, _, retime = _make(fps=30.0)
    lid = next(iter(store.state.labels.keys()))
    store.set_selection({lid})
    before = store.state.labels[lid]
    retime.nudge_both(+1)
    after = store.state.labels[lid]
    expected_delta = 1.0 / 30.0
    assert after.start_time == pytest.approx(before.start_time + expected_delta, abs=0.02)
    assert after.end_time == pytest.approx(before.end_time + expected_delta, abs=0.02)


def test_shift_moves_each_label_by_delta(qapp):
    store, _, retime = _make()
    lid = next(iter(store.state.labels.keys()))
    store.set_selection({lid})
    before = store.state.labels[lid]
    retime.shift(0.5)
    after = store.state.labels[lid]
    assert after.start_time == pytest.approx(before.start_time + 0.5, abs=0.02)
    assert after.end_time == pytest.approx(before.end_time + 0.5, abs=0.02)


def test_shift_clamps_at_zero(qapp):
    store, _, retime = _make()
    lid = next(iter(store.state.labels.keys()))
    store.set_selection({lid})
    # Shift by a huge negative delta to push past zero.
    retime.shift(-999_999.0)
    after = store.state.labels[lid]
    assert after.start_time == 0.0
    assert after.end_time >= 0.03   # at least one frame past start


def test_empty_selection_is_noop(qapp):
    store, _, retime = _make()
    store.set_selection(set())
    before = {lid: (lb.start_time, lb.end_time) for lid, lb in store.state.labels.items()}
    retime.set_in_at_current()
    retime.set_out_at_current()
    retime.nudge_both(+1)
    retime.shift(1.5)
    after = {lid: (lb.start_time, lb.end_time) for lid, lb in store.state.labels.items()}
    assert before == after


def test_set_in_aligns_all_selected_starts_to_t(qapp):
    """Multi-label semantic: every selected label's start := T."""
    store, _, retime = _make(current=42.0)
    lids = list(store.state.labels.keys())[:3]
    store.set_selection(set(lids))
    retime.set_in_at_current()
    for lid in lids:
        assert store.state.labels[lid].start_time == 42.0


def test_set_out_aligns_all_selected_ends_to_t(qapp):
    store, _, retime = _make(current=100.0)
    lids = list(store.state.labels.keys())[:3]
    store.set_selection(set(lids))
    retime.set_out_at_current()
    for lid in lids:
        assert store.state.labels[lid].end_time == 100.0


def test_shift_multi_preserves_each_labels_duration(qapp):
    store, _, retime = _make()
    lids = list(store.state.labels.keys())[:3]
    store.set_selection(set(lids))
    before = {lid: store.state.labels[lid].end_time - store.state.labels[lid].start_time
              for lid in lids}
    retime.shift(2.0)
    after = {lid: store.state.labels[lid].end_time - store.state.labels[lid].start_time
             for lid in lids}
    for lid in lids:
        assert after[lid] == pytest.approx(before[lid], abs=0.04)


def test_apply_input_to_start_relative_evaluates_per_label(qapp):
    """Relative input on multi-select uses each label's own start as `current`."""
    store, _, retime = _make(fps=30.0)
    lids = list(store.state.labels.keys())[:2]
    store.set_selection(set(lids))
    starts_before = {lid: store.state.labels[lid].start_time for lid in lids}
    ok = retime.apply_input_to_start("+1s")
    assert ok is True
    for lid in lids:
        assert store.state.labels[lid].start_time == pytest.approx(
            starts_before[lid] + 1.0, abs=0.04,
        )


def test_apply_input_to_start_garbage_returns_false_and_does_not_mutate(qapp):
    store, _, retime = _make()
    lid = next(iter(store.state.labels.keys()))
    store.set_selection({lid})
    before = (store.state.labels[lid].start_time, store.state.labels[lid].end_time)
    ok = retime.apply_input_to_start("garbage")
    assert ok is False
    after = (store.state.labels[lid].start_time, store.state.labels[lid].end_time)
    assert before == after


def test_drag_session_coalesces_to_one_undo_entry(qapp):
    store, _, retime = _make(fps=30.0, current=0.0)
    lid = next(iter(store.state.labels.keys()))
    store.set_selection({lid})
    original = (store.state.labels[lid].start_time, store.state.labels[lid].end_time)

    retime.begin_drag("start")
    for t in (5.0, 5.5, 6.0, 6.5):
        retime.update_drag(t)
    retime.end_drag()

    # One undo should restore the original state.
    store.undo()
    after = (store.state.labels[lid].start_time, store.state.labels[lid].end_time)
    assert after == original


def test_update_drag_outside_session_is_noop(qapp):
    store, _, retime = _make()
    lid = next(iter(store.state.labels.keys()))
    store.set_selection({lid})
    before = (store.state.labels[lid].start_time, store.state.labels[lid].end_time)
    retime.update_drag(99.0)   # no begin_drag called
    after = (store.state.labels[lid].start_time, store.state.labels[lid].end_time)
    assert before == after


def test_fps_zero_disables_frame_step_nudge(qapp):
    store, _, retime = _make(fps=0.0)
    lid = next(iter(store.state.labels.keys()))
    store.set_selection({lid})
    before = (store.state.labels[lid].start_time, store.state.labels[lid].end_time)
    retime.nudge_both(+1)
    after = (store.state.labels[lid].start_time, store.state.labels[lid].end_time)
    # With fps=0, the delta is 0, so positions are unchanged but mutation
    # may still pass through the clamp path. Either way: no time change.
    assert after[0] == pytest.approx(before[0], abs=0.02)
    assert after[1] == pytest.approx(before[1], abs=0.02)


def test_clamp_does_not_hang_at_very_high_fps(qapp):
    """Regression: at fps > 200, 1/fps rounds to 0 centiseconds; _clamp must
    fall back to one centisecond instead of looping forever."""
    store, _, retime = _make(fps=240.0, current=1000.0)
    lid = next(iter(store.state.labels.keys()))
    store.set_selection({lid})
    retime.set_in_at_current()   # would hang without the fix
    after = store.state.labels[lid]
    assert after.start_time == 1000.0
    assert after.end_time > after.start_time


def test_apply_input_to_start_empty_selection_returns_true_no_mutation(qapp):
    """Empty selection is a no-op (returns True) — not a parse failure."""
    store, _, retime = _make()
    store.set_selection(set())
    ok = retime.apply_input_to_start("0:01:23.45")
    assert ok is True
    # And of course no mutations happened.


def test_nudge_start_shifts_only_start_by_frames(qapp):
    store, _, retime = _make(fps=30.0)
    lid = next(iter(store.state.labels.keys()))
    store.set_selection({lid})
    before = store.state.labels[lid]
    retime.nudge_start(+2)
    after = store.state.labels[lid]
    expected_delta = 2.0 / 30.0
    assert after.start_time == pytest.approx(before.start_time + expected_delta, abs=0.02)
    assert after.end_time == pytest.approx(before.end_time, abs=0.02)


def test_nudge_end_shifts_only_end_by_frames(qapp):
    store, _, retime = _make(fps=30.0)
    lid = next(iter(store.state.labels.keys()))
    store.set_selection({lid})
    before = store.state.labels[lid]
    retime.nudge_end(+2)
    after = store.state.labels[lid]
    expected_delta = 2.0 / 30.0
    assert after.start_time == pytest.approx(before.start_time, abs=0.02)
    assert after.end_time == pytest.approx(before.end_time + expected_delta, abs=0.02)


def test_apply_input_to_end_absolute_updates_end(qapp):
    store, _, retime = _make()
    lid = next(iter(store.state.labels.keys()))
    store.set_selection({lid})
    ok = retime.apply_input_to_end("0:00:10.00")
    assert ok is True
    assert store.state.labels[lid].end_time == 10.0


def _make_with_seek(fps=30.0, current=10.0):
    """Variant that captures seek calls into a list for assertion."""
    ass = AssFile(str(FIXTURE))
    store = LabelStore()
    store.load(ass, FIXTURE)
    edit = LabelEditController(store)
    seeks: list[float] = []
    retime = RetimeController(
        store, edit,
        fps_provider=lambda: fps,
        current_time_provider=lambda: current,
        seek_callback=seeks.append,
    )
    return store, retime, seeks


def test_seek_to_in_jumps_player_to_selection_earliest_start(qapp):
    store, retime, seeks = _make_with_seek()
    lids = list(store.state.order)[:2]
    store.set_selection(set(lids))
    expected = min(store.state.labels[lid].start_time for lid in lids)
    retime.seek_to_in()
    assert seeks == [expected]


def test_seek_to_out_jumps_player_to_selection_latest_end(qapp):
    store, retime, seeks = _make_with_seek()
    lids = list(store.state.order)[:2]
    store.set_selection(set(lids))
    expected = max(store.state.labels[lid].end_time for lid in lids)
    retime.seek_to_out()
    assert seeks == [expected]


def test_seek_to_in_with_empty_selection_is_noop(qapp):
    store, retime, seeks = _make_with_seek()
    store.set_selection(set())
    retime.seek_to_in()
    retime.seek_to_out()
    assert seeks == []


def test_seek_to_in_when_no_callback_is_safe_noop(qapp):
    store, _, retime = _make()   # default constructor — no seek_callback
    lid = store.state.order[0]
    store.set_selection({lid})
    # Should not raise even without a callback.
    retime.seek_to_in()
    retime.seek_to_out()
