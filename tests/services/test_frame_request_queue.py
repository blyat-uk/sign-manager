"""Tests for FrameRequestQueue."""

import time
from pathlib import Path

import pytest
from PyQt6.QtCore import QCoreApplication, QEventLoop, QTimer

from sign_manager.services.frame_request_queue import FrameRequestQueue


@pytest.fixture(scope="session")
def qapp():
    app = QCoreApplication.instance() or QCoreApplication([])
    yield app


def _spin(timeout_ms=1500):
    """Pump the Qt event loop briefly so worker signals can be delivered."""
    loop = QEventLoop()
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()


class _SuccessSvc:
    """Fake VideoService that returns a sentinel after a tiny delay."""
    def get_frame(self, path, seconds):
        time.sleep(0.02)
        return f"frame@{seconds}"


class _FailSvc:
    def get_frame(self, path, seconds):
        from sign_manager.services.exceptions import FrameExtractionError
        raise FrameExtractionError("bad")


def test_request_returns_monotonic_sequence_number(qapp):
    q = FrameRequestQueue(_SuccessSvc(), workers=1)
    seq1 = q.request(Path("/x.mkv"), 1.0)
    seq2 = q.request(Path("/x.mkv"), 2.0)
    seq3 = q.request(Path("/x.mkv"), 3.0)
    assert seq2 > seq1
    assert seq3 > seq2


def test_request_delivers_via_signal(qapp):
    q = FrameRequestQueue(_SuccessSvc(), workers=1)
    received = []
    q.frame_ready.connect(lambda seq, img: received.append((seq, img)))
    seq = q.request(Path("/x.mkv"), 2.5)
    _spin(500)
    q.shutdown()
    assert received, "no frame_ready signal received"
    assert received[0][0] == seq
    assert received[0][1] == "frame@2.5"


def test_failure_emits_frame_failed(qapp):
    q = FrameRequestQueue(_FailSvc(), workers=1)
    failed = []
    ready = []
    q.frame_failed.connect(lambda seq, err: failed.append((seq, err)))
    q.frame_ready.connect(lambda seq, img: ready.append((seq, img)))
    seq = q.request(Path("/x.mkv"), 1.0)
    _spin(500)
    q.shutdown()
    assert ready == []
    assert failed
    assert failed[0][0] == seq
    assert "bad" in failed[0][1]


def test_shutdown_waits_for_pending_tasks(qapp):
    q = FrameRequestQueue(_SuccessSvc(), workers=2)
    for _ in range(4):
        q.request(Path("/x.mkv"), 1.0)
    # Should not raise; should wait for pending tasks to complete
    q.shutdown(wait_ms=2000)
    # After shutdown, no more requests accepted (subsequent request still
    # returns a sequence number; whether it runs depends on impl — we don't
    # assert that here).
