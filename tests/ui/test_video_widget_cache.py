"""Tests for VideoFrameWidget cache + frame_queue wiring."""

from pathlib import Path
from PyQt6.QtCore import QObject, pyqtSignal

from sub_label_pos.model.label_store import LabelStore


class _FakeFrameQueue(QObject):
    frame_ready = pyqtSignal(int, object)
    frame_failed = pyqtSignal(int, str)

    def __init__(self):
        super().__init__()
        self.requests = []
        self._seq = 0

    def request(self, path, seconds, *, max_dim=None, jpeg_quality=None):
        self._seq += 1
        self.requests.append((path, seconds, max_dim, jpeg_quality, self._seq))
        return self._seq

    def shutdown(self, wait_ms=0):
        pass


class _FakeSvc:
    def get_frame(self, path, seconds, *, max_dim=None, jpeg_quality=None):
        return None

    def get_dimensions(self, path):
        return (1920, 1080)

    def get_duration(self, path):
        return 10.0

    def get_fps(self, path):
        return 30.0


def test_cache_max_honours_constructor_arg(qapp):
    from sub_label_pos.ui.video_widget import VideoFrameWidget
    store = LabelStore()
    fq = _FakeFrameQueue()
    w = VideoFrameWidget(store, _FakeSvc(), frame_queue=fq, frame_cache_size=8)
    assert w._cache_max == 8


def test_target_max_dim_rounds_to_256(qapp):
    from sub_label_pos.ui.video_widget import VideoFrameWidget
    store = LabelStore()
    fq = _FakeFrameQueue()
    w = VideoFrameWidget(store, _FakeSvc(), frame_queue=fq, frame_cache_size=16)
    # default before show
    assert w._target_max_dim in (480, 1280)


def test_show_time_cache_miss_dispatches_async(qapp, tmp_path):
    from sub_label_pos.ui.video_widget import VideoFrameWidget
    store = LabelStore()
    fq = _FakeFrameQueue()
    w = VideoFrameWidget(store, _FakeSvc(), frame_queue=fq, frame_cache_size=4)
    w._video_path = str(tmp_path / "fake.mkv")
    w.show_time(5.0)
    assert len(fq.requests) == 1
    assert fq.requests[0][1] == 5.0
    assert fq.requests[0][2] == w._target_max_dim
    assert w._pending_seek_seq == 1


def test_async_frame_drops_stale_seq(qapp, tmp_path):
    from sub_label_pos.ui.video_widget import VideoFrameWidget
    store = LabelStore()
    fq = _FakeFrameQueue()
    w = VideoFrameWidget(store, _FakeSvc(), frame_queue=fq, frame_cache_size=4)
    w._video_path = str(tmp_path / "fake.mkv")
    w.show_time(5.0)   # seq 1
    w.show_time(7.0)   # seq 2
    # An old seq-1 result should be ignored
    initial_cache_size = len(w._cache)
    w._on_async_frame_ready(1, object())
    assert len(w._cache) == initial_cache_size, "stale seq should not populate cache"


def test_render_cache_populates_and_reuses(qapp):
    """First paintEvent on a label builds and caches; subsequent paints hit cache."""
    from pathlib import Path
    from sub_label_pos.ui.video_widget import VideoFrameWidget
    from sub_label_pos.model.ass_file import AssFile
    store = LabelStore()
    fq = _FakeFrameQueue()
    w = VideoFrameWidget(store, _FakeSvc(), frame_queue=fq, frame_cache_size=4)
    fixture = Path(__file__).parent.parent / "fixtures" / "sample.ass"
    ass = AssFile.from_path(fixture)
    w.set_ass(ass)
    store.load(ass, source_path=fixture)
    w._update_visible_labels(2.0)
    # Build cache for first visible label
    lid = w._visible_labels[0].label_id
    rc = w._build_render_cache(w._visible_labels[0])
    w._render_cache[lid] = rc
    assert lid in w._render_cache
    # Mutate that label -> cache entry should be popped
    from sub_label_pos.model.mutations import MoveLabel
    store.apply(MoveLabel(label_id=lid, new_x=999, new_y=999))
    assert lid not in w._render_cache


def test_render_cache_cleared_on_file_load(qapp):
    from pathlib import Path
    from sub_label_pos.ui.video_widget import VideoFrameWidget
    from sub_label_pos.model.ass_file import AssFile
    store = LabelStore()
    fq = _FakeFrameQueue()
    w = VideoFrameWidget(store, _FakeSvc(), frame_queue=fq, frame_cache_size=4)
    fixture = Path(__file__).parent.parent / "fixtures" / "sample.ass"
    ass = AssFile.from_path(fixture)
    w.set_ass(ass)
    store.load(ass, source_path=fixture)
    # Force a couple of entries into the cache
    for label in store.state.labels.values():
        w._render_cache[label.label_id] = object()  # marker
    assert len(w._render_cache) > 0
    # Reload — should clear
    store.load(ass, source_path=fixture)
    assert len(w._render_cache) == 0


def test_drag_layer_starts_none(qapp):
    """Issue M: drag layer defaults to empty / no dragged ids."""
    from sub_label_pos.ui.video_widget import VideoFrameWidget
    store = LabelStore()
    fq = _FakeFrameQueue()
    w = VideoFrameWidget(store, _FakeSvc(), frame_queue=fq, frame_cache_size=4)
    assert w._drag_layer is None
    assert w._drag_layer_dragged_ids == frozenset()


def test_drag_layer_invalidates_on_scaled_pixmap_change(qapp):
    """Issue M: changing the frame pixmap clears the stale drag layer."""
    from sub_label_pos.ui.video_widget import VideoFrameWidget
    from PyQt6.QtGui import QPixmap
    store = LabelStore()
    fq = _FakeFrameQueue()
    w = VideoFrameWidget(store, _FakeSvc(), frame_queue=fq, frame_cache_size=4)
    # Stub a layer + dragged set so we can observe invalidation.
    w._drag_layer = QPixmap(100, 100)
    w._drag_layer_dragged_ids = frozenset({"x"})
    # Set a fake pixmap and call _update_scaled_pixmap; layer should clear.
    w._pixmap = QPixmap(800, 450)
    w.resize(800, 450)
    w._update_scaled_pixmap()
    assert w._drag_layer is None
    assert w._drag_layer_dragged_ids == frozenset()


def test_drag_layer_invalidates_on_file_load(qapp):
    """Issue M: loading a new file drops the stale drag layer."""
    from pathlib import Path
    from sub_label_pos.ui.video_widget import VideoFrameWidget
    from sub_label_pos.model.ass_file import AssFile
    from PyQt6.QtGui import QPixmap
    store = LabelStore()
    fq = _FakeFrameQueue()
    w = VideoFrameWidget(store, _FakeSvc(), frame_queue=fq, frame_cache_size=4)
    w._drag_layer = QPixmap(100, 100)
    w._drag_layer_dragged_ids = frozenset({"x"})
    fixture = Path(__file__).parent.parent / "fixtures" / "sample.ass"
    store.load(AssFile.from_path(fixture), source_path=fixture)
    assert w._drag_layer is None
    assert w._drag_layer_dragged_ids == frozenset()


def test_drag_layer_invalidates_on_set_ass(qapp):
    """Issue M: swapping the AssFile drops the stale drag layer."""
    from pathlib import Path
    from sub_label_pos.ui.video_widget import VideoFrameWidget
    from sub_label_pos.model.ass_file import AssFile
    from PyQt6.QtGui import QPixmap
    store = LabelStore()
    fq = _FakeFrameQueue()
    w = VideoFrameWidget(store, _FakeSvc(), frame_queue=fq, frame_cache_size=4)
    w._drag_layer = QPixmap(100, 100)
    w._drag_layer_dragged_ids = frozenset({"x"})
    fixture = Path(__file__).parent.parent / "fixtures" / "sample.ass"
    w.set_ass(AssFile.from_path(fixture))
    assert w._drag_layer is None
    assert w._drag_layer_dragged_ids == frozenset()


def test_drag_layer_invalidates_on_resize(qapp):
    """Issue M: widget resize invalidates the drag layer explicitly."""
    from sub_label_pos.ui.video_widget import VideoFrameWidget
    from PyQt6.QtGui import QPixmap
    from PyQt6.QtCore import QSize
    from PyQt6.QtGui import QResizeEvent
    store = LabelStore()
    fq = _FakeFrameQueue()
    w = VideoFrameWidget(store, _FakeSvc(), frame_queue=fq, frame_cache_size=4)
    w._drag_layer = QPixmap(100, 100)
    w._drag_layer_dragged_ids = frozenset({"x"})
    # Invoke resizeEvent directly -- the QWidget.resize() call doesn't fire
    # resizeEvent until the widget is shown / laid out by the event loop.
    w.resizeEvent(QResizeEvent(QSize(900, 500), QSize(800, 450)))
    assert w._drag_layer is None
    assert w._drag_layer_dragged_ids == frozenset()


def test_drag_layer_kept_when_only_dragged_id_mutated(qapp):
    """Issue M: mutations on the dragged id alone do NOT invalidate the layer
    (the layer caches everything OTHER than the dragged labels)."""
    from sub_label_pos.ui.video_widget import VideoFrameWidget
    from PyQt6.QtGui import QPixmap
    store = LabelStore()
    fq = _FakeFrameQueue()
    w = VideoFrameWidget(store, _FakeSvc(), frame_queue=fq, frame_cache_size=4)
    sentinel = QPixmap(100, 100)
    w._drag_layer = sentinel
    w._drag_layer_dragged_ids = frozenset({"dragged"})
    # Fire the slot directly with a set containing only the dragged id.
    w._on_store_labels_mutated({"dragged"})
    assert w._drag_layer is sentinel
    assert w._drag_layer_dragged_ids == frozenset({"dragged"})


def test_frame_prefetch_worker_threads_max_dim(qapp, tmp_path):
    """Fix 1: FramePrefetchWorker passes max_dim through to get_frame so
    prefetched pixmaps live in the same downscaled bucket as the editor's
    sync/async paths."""
    from sub_label_pos.ui.video_widget import FramePrefetchWorker

    calls: list[tuple] = []

    class _RecordingSvc:
        def get_frame(self, path, t, *, max_dim=None, jpeg_quality=None):
            calls.append((path, t, max_dim))
            return None

    worker = FramePrefetchWorker(
        _RecordingSvc(), str(tmp_path / "fake.mkv"), [0.0, 0.04, 0.08],
        max_dim=1280,
    )
    worker.run()
    assert calls, "worker should have called get_frame"
    assert all(c[2] == 1280 for c in calls), \
        f"every prefetch call should request max_dim=1280, got {calls}"


def test_drag_layer_invalidated_when_non_dragged_id_mutated(qapp):
    """Issue M: mutations on a non-dragged id mid-gesture invalidate the
    layer so the next paintEvent rebuilds it with the fresh appearance."""
    from sub_label_pos.ui.video_widget import VideoFrameWidget
    from PyQt6.QtGui import QPixmap
    store = LabelStore()
    fq = _FakeFrameQueue()
    w = VideoFrameWidget(store, _FakeSvc(), frame_queue=fq, frame_cache_size=4)
    w._drag_layer = QPixmap(100, 100)
    w._drag_layer_dragged_ids = frozenset({"dragged"})
    w._on_store_labels_mutated({"other"})
    assert w._drag_layer is None
    assert w._drag_layer_dragged_ids == frozenset()


def _loaded_widget(qapp):
    """Build a VideoFrameWidget with the sample fixture loaded into its store."""
    from sub_label_pos.model.ass_file import AssFile
    from sub_label_pos.ui.video_widget import VideoFrameWidget
    fixture = Path(__file__).parent.parent / "fixtures" / "sample.ass"
    ass = AssFile(str(fixture))
    store = LabelStore()
    store.load(ass, fixture)
    fq = _FakeFrameQueue()
    w = VideoFrameWidget(store, _FakeSvc(), frame_queue=fq, frame_cache_size=4)
    return w, store


def test_seek_outside_label_window_preserves_selection(qapp):
    """Selection persists when the playhead leaves a selected label's window.
    Previously the canvas auto-pruned selection to visible labels only; the
    RetimeBar / focused timeline retiming flow requires the selection to
    survive seeking outside the label's current start/end."""
    w, store = _loaded_widget(qapp)
    lid = store.state.order[0]
    label = store.state.labels[lid]
    store.set_selection({lid})
    # Pick a time well outside this label's window.
    outside_t = label.end_time + 100.0
    w._update_visible_labels(outside_t)
    assert lid in store.selected, "selection must persist across scrubbing"
    assert label in w._ghost_labels, "out-of-window selected label should be a ghost"
    assert label not in w._visible_labels, "out-of-window label must not be in visible list"


def test_seek_back_into_label_window_promotes_ghost_to_visible(qapp):
    """A ghost label returns to the visible list when the playhead re-enters
    its window. Selection survives both transitions."""
    w, store = _loaded_widget(qapp)
    lid = store.state.order[0]
    label = store.state.labels[lid]
    store.set_selection({lid})
    w._update_visible_labels(label.end_time + 100.0)
    assert label in w._ghost_labels
    # Seek back into the label's window.
    inside_t = (label.start_time + label.end_time) / 2.0
    w._update_visible_labels(inside_t)
    assert label in w._visible_labels
    assert label not in w._ghost_labels
    assert lid in store.selected


def test_unselected_label_outside_window_is_not_a_ghost(qapp):
    """Only SELECTED out-of-window labels become ghosts. Unselected ones are
    simply absent from both lists at that time."""
    w, store = _loaded_widget(qapp)
    lid = store.state.order[0]
    label = store.state.labels[lid]
    # No selection at all.
    store.set_selection(set())
    w._update_visible_labels(label.end_time + 100.0)
    assert label not in w._visible_labels
    assert label not in w._ghost_labels
