"""Label rects must be keyed by LabelId, not by the stale ``line_index``.

``line_index`` records a label's position in the *parsed* file and is not
maintained as unique across structural mutations: ``DuplicateLabel`` copies
it from the source verbatim, and ``create_label`` derives it from
``len(state.order)``. Keying the per-paint hit-test dict by it therefore
makes two labels share one entry, and the loser becomes unclickable.
"""

from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QPainter, QPixmap

from sub_label_pos.model.ass_file import AssFile
from sub_label_pos.model.label_store import LabelStore
from sub_label_pos.model.mutations import DuplicateLabel
from sub_label_pos.model.types import LabelId

FIXTURE = Path(__file__).parent.parent / "fixtures" / "sample.ass"


class _FakeFrameQueue(QObject):
    frame_ready = pyqtSignal(int, object)
    frame_failed = pyqtSignal(int, str)

    def request(self, path, seconds, *, max_dim=None, jpeg_quality=None):
        return 0

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


def _make_widget():
    from sub_label_pos.ui.video_widget import VideoFrameWidget

    store = LabelStore()
    ass = AssFile.from_path(FIXTURE)
    store.load(ass, source_path=FIXTURE)
    w = VideoFrameWidget(store, _FakeSvc(), frame_queue=_FakeFrameQueue())
    w.resize(1920, 1080)
    w.set_ass(ass)
    # Stand in for a loaded frame: 1:1 mapping between ASS and widget coords.
    w._frame_x, w._frame_y, w._frame_w, w._frame_h = 0, 0, 1920, 1080
    return store, w


def _paint(w):
    """Run _paint_labels offscreen so _label_rects is populated."""
    pm = QPixmap(w.width(), w.height())
    painter = QPainter(pm)
    w._paint_labels(painter, draw_handles=False)
    painter.end()


def _center_of(w, label):
    rect = w._compute_rect(label, w._font_for_label(label))
    return rect.center()


def test_duplicate_keeps_source_hit_testable(qapp):
    store, w = _make_widget()
    src_id = store.state.order[0]
    dup_id = LabelId("dup-1")
    # Offset far enough that the two rects cannot overlap, so a hit at the
    # source's centre is unambiguously the source.
    store.apply(DuplicateLabel(
        source_label_id=src_id, new_label_id=dup_id, position_offset=(600.0, 400.0),
    ))
    w.show_time(2.0)
    _paint(w)

    src = store.state.labels[src_id]
    dup = store.state.labels[dup_id]
    assert w._hit_test(_center_of(w, dup)) is not None
    assert w._hit_test(_center_of(w, dup)).label_id == dup_id
    hit = w._hit_test(_center_of(w, src))
    assert hit is not None, "source label became unclickable after duplicate"
    assert hit.label_id == src_id


def test_every_visible_label_gets_its_own_rect(qapp):
    store, w = _make_widget()
    src_id = store.state.order[0]
    store.apply(DuplicateLabel(
        source_label_id=src_id, new_label_id=LabelId("dup-1"),
        position_offset=(600.0, 400.0),
    ))
    w.show_time(2.0)
    _paint(w)
    assert len(w._label_rects) == len(w._visible_labels)


def test_multi_drag_preserves_relative_offset_after_duplicate(qapp):
    """A multi-select drag keys initial positions by id too.

    With ``line_index`` as the key, the source and its duplicate shared one
    entry, so both were rebased onto the same initial position and collapsed
    on top of each other.
    """
    from PyQt6.QtCore import QPointF

    store, w = _make_widget()
    src_id = store.state.order[0]
    dup_id = LabelId("dup-1")
    store.apply(DuplicateLabel(
        source_label_id=src_id, new_label_id=dup_id, position_offset=(600.0, 400.0),
    ))
    w.show_time(2.0)
    _paint(w)

    src = store.state.labels[src_id]
    dup = store.state.labels[dup_id]
    offset_before = (dup.pos_x - src.pos_x, dup.pos_y - src.pos_y)

    store.set_selection({src_id, dup_id})
    w._press_label = src
    w._press_pos = _center_of(w, src)
    w._start_drag(w._press_pos)
    w._do_drag_move(w._press_pos + QPointF(120.0, 90.0))

    offset_after = (dup.pos_x - src.pos_x, dup.pos_y - src.pos_y)
    assert offset_after == offset_before
    assert (src.pos_x, src.pos_y) != (dup.pos_x, dup.pos_y)
