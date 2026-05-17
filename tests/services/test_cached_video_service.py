"""Tests for CachedVideoService decorator."""

import pytest
from pathlib import Path

from sub_label_pos.services.frame_cache import FrameCache
from sub_label_pos.services.video_service import CachedVideoService
from sub_label_pos.services.exceptions import FrameExtractionError


def test_cached_get_frame_hits_inner_once(mocker):
    inner = mocker.Mock()
    inner.get_frame.return_value = object()
    svc = CachedVideoService(inner, FrameCache(max_size=4))
    svc.get_frame(Path("/x.mkv"), 1.0)
    svc.get_frame(Path("/x.mkv"), 1.0)
    assert inner.get_frame.call_count == 1


def test_cached_different_keys_call_inner_each(mocker):
    inner = mocker.Mock()
    inner.get_frame.return_value = object()
    svc = CachedVideoService(inner, FrameCache(max_size=4))
    svc.get_frame(Path("/x.mkv"), 1.0)
    svc.get_frame(Path("/x.mkv"), 2.0)
    svc.get_frame(Path("/y.mkv"), 1.0)
    assert inner.get_frame.call_count == 3


def test_cached_passthrough_for_uncached_methods(mocker):
    inner = mocker.Mock()
    inner.get_dimensions.return_value = (1920, 1080)
    inner.get_duration.return_value = 12.5
    inner.get_fps.return_value = 30.0
    svc = CachedVideoService(inner, FrameCache(max_size=4))
    assert svc.get_dimensions(Path("/x")) == (1920, 1080)
    assert svc.get_duration(Path("/x")) == 12.5
    assert svc.get_fps(Path("/x")) == 30.0
    inner.get_dimensions.assert_called_once_with(Path("/x"))
    inner.get_duration.assert_called_once_with(Path("/x"))
    inner.get_fps.assert_called_once_with(Path("/x"))


def test_cached_get_frame_propagates_exceptions(mocker):
    inner = mocker.Mock()
    inner.get_frame.side_effect = FrameExtractionError("boom")
    svc = CachedVideoService(inner, FrameCache(max_size=4))
    with pytest.raises(FrameExtractionError):
        svc.get_frame(Path("/x.mkv"), 1.0)
    # Failed calls should NOT be cached
    inner.get_frame.side_effect = None
    inner.get_frame.return_value = "ok"
    assert svc.get_frame(Path("/x.mkv"), 1.0) == "ok"
    assert inner.get_frame.call_count == 2


def test_cached_get_frame_rounds_seconds_for_key():
    """Two seconds values within 1ms should hit the same cache entry."""
    class Fake:
        calls = 0
        def get_frame(self, path, seconds):
            Fake.calls += 1
            return f"frame-{seconds}"
    inner = Fake()
    svc = CachedVideoService(inner, FrameCache(max_size=4))
    svc.get_frame(Path("/x.mkv"), 1.0)
    svc.get_frame(Path("/x.mkv"), 1.0001)   # rounded to 1.000
    svc.get_frame(Path("/x.mkv"), 1.0009)   # rounded to 1.001
    # First two should hit cache once; third is a separate key
    assert inner.calls == 2
