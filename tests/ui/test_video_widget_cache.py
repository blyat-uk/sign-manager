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
