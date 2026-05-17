"""FrameRequestQueue — dispatches video frame requests to a QThreadPool.

Widgets post requests and receive results via signals on the main thread.
Sequence numbers let callers discard stale results.
"""

from __future__ import annotations

import itertools
import logging
from pathlib import Path

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal

from sub_label_pos.services.video_service import VideoService

log = logging.getLogger(__name__)


class _FrameTask(QRunnable):
    def __init__(self, svc: VideoService, path: Path, seconds: float,
                 seq: int, emitter: "FrameRequestQueue") -> None:
        super().__init__()
        self._svc = svc
        self._path = path
        self._seconds = seconds
        self._seq = seq
        self._emitter = emitter

    def run(self) -> None:
        try:
            img = self._svc.get_frame(self._path, self._seconds)
            self._emitter.frame_ready.emit(self._seq, img)
        except Exception as e:
            log.warning("frame %d failed for %s @ %s: %s",
                        self._seq, self._path, self._seconds, e)
            self._emitter.frame_failed.emit(self._seq, str(e))


class FrameRequestQueue(QObject):
    """Off-UI-thread frame extraction.

    Signals:
      frame_ready(seq, image): a request completed
      frame_failed(seq, message): a request raised
    """

    frame_ready = pyqtSignal(int, object)
    frame_failed = pyqtSignal(int, str)

    def __init__(self, video_service: VideoService, *, workers: int = 2) -> None:
        super().__init__()
        self._svc = video_service
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(workers)
        self._counter = itertools.count(1)

    def request(self, path: Path, seconds: float) -> int:
        """Enqueue a request. Returns a monotonic sequence number used by the
        caller to filter stale results.

        Callers should track the most recent sequence number they issued and
        ignore frame_ready/frame_failed signals whose seq does not match — those
        are stale results from earlier requests the user has moved past.
        """
        seq = next(self._counter)
        self._pool.start(_FrameTask(self._svc, path, seconds, seq, self))
        return seq

    def shutdown(self, wait_ms: int = 2000) -> None:
        """Wait for pending tasks to finish (or up to wait_ms)."""
        self._pool.clear()
        self._pool.waitForDone(wait_ms)
