"""Tests for FilePreloadTask."""

from pathlib import Path

import pytest

from sub_label_pos.services.exceptions import (
    FrameExtractionError,
    VideoFormatError,
)
from sub_label_pos.services.preload import (
    FilePreloadTask,
    PreloadSignals,
)


@pytest.fixture(scope="session")
def qapp():
    from PyQt6.QtCore import QCoreApplication

    app = QCoreApplication.instance() or QCoreApplication([])
    yield app


def _run_sync(task: FilePreloadTask) -> None:
    """Run a FilePreloadTask synchronously (skip threadpool for test simplicity)."""
    task.run()


class _AllOK:
    def get_duration(self, p):
        return 12.5

    def get_dimensions(self, p):
        return (1920, 1080)

    def get_fps(self, p):
        return 30.0

    def get_frame(self, p, s):
        return "frame"


class _AllFail:
    def get_duration(self, p):
        raise VideoFormatError("no")

    def get_dimensions(self, p):
        raise VideoFormatError("no")

    def get_fps(self, p):
        raise VideoFormatError("no")

    def get_frame(self, p, s):
        raise FrameExtractionError("no")


class _PartialFail:
    def get_duration(self, p):
        return 12.5

    def get_dimensions(self, p):
        raise VideoFormatError("dims bad")

    def get_fps(self, p):
        return 30.0

    def get_frame(self, p, s):
        return "frame"


def test_success_populates_all_fields(qapp):
    received = []
    sigs = PreloadSignals()
    sigs.completed.connect(received.append)
    task = FilePreloadTask(Path("/x.mkv"), _AllOK(), sigs)
    _run_sync(task)
    assert len(received) == 1
    r = received[0]
    assert r.duration == 12.5
    assert r.dimensions == (1920, 1080)
    assert r.fps == 30.0
    assert r.first_frame == "frame"
    assert r.errors == []


def test_all_failures_still_emit_result_with_errors(qapp):
    received = []
    sigs = PreloadSignals()
    sigs.completed.connect(received.append)
    task = FilePreloadTask(Path("/x.mkv"), _AllFail(), sigs)
    _run_sync(task)
    assert len(received) == 1
    r = received[0]
    assert r.duration is None
    assert r.dimensions is None
    assert r.fps is None
    assert r.first_frame is None
    assert len(r.errors) == 4  # one per field


def test_partial_failure_does_not_abort_batch(qapp):
    """If one field fails, the other fields are still populated."""
    received = []
    sigs = PreloadSignals()
    sigs.completed.connect(received.append)
    task = FilePreloadTask(Path("/x.mkv"), _PartialFail(), sigs)
    _run_sync(task)
    r = received[0]
    assert r.duration == 12.5
    assert r.dimensions is None  # failed
    assert r.fps == 30.0  # still ran
    assert r.first_frame == "frame"  # still ran
    assert len(r.errors) == 1
    assert "dims bad" in r.errors[0]


def test_cancel_before_run_emits_nothing(qapp):
    received = []
    sigs = PreloadSignals()
    sigs.completed.connect(received.append)
    task = FilePreloadTask(Path("/x.mkv"), _AllOK(), sigs)
    task.cancel()
    _run_sync(task)
    assert received == []
