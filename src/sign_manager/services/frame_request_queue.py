"""FrameRequestQueue — dispatches video frame requests to a QThreadPool.

Widgets post requests and receive results via signals on the main thread.
Sequence numbers let callers discard stale results.
"""

from __future__ import annotations

import itertools
import logging
from pathlib import Path

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal

from sign_manager.services.video_service import VideoService

log = logging.getLogger(__name__)


class _FrameTask(QRunnable):
    def __init__(self, svc: VideoService, path: Path, seconds: float,
                 seq: int, emitter: "FrameRequestQueue",
                 max_dim: int | None = None,
                 jpeg_quality: int | None = None) -> None:
        super().__init__()
        self._svc = svc
        self._path = path
        self._seconds = seconds
        self._seq = seq
        self._emitter = emitter
        self._max_dim = max_dim
        self._jpeg_quality = jpeg_quality

    def run(self) -> None:
        try:
            # Forward optional sizing/quality kwargs only when set so simple
            # fake services in tests (which accept only path+seconds) keep
            # working unchanged.
            kwargs: dict = {}
            if self._max_dim is not None:
                kwargs["max_dim"] = self._max_dim
            if self._jpeg_quality is not None:
                kwargs["jpeg_quality"] = self._jpeg_quality
            img = self._svc.get_frame(self._path, self._seconds, **kwargs)
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

    def request(self, path: Path, seconds: float, *,
                max_dim: int | None = None,
                jpeg_quality: int | None = None) -> int:
        """Enqueue a request. Returns a monotonic sequence number used by the
        caller to filter stale results.

        Callers should track the most recent sequence number they issued and
        ignore frame_ready/frame_failed signals whose seq does not match — those
        are stale results from earlier requests the user has moved past.

        ``max_dim`` and ``jpeg_quality`` are forwarded to ``VideoService.get_frame``
        when set; cache keying in ``CachedVideoService`` already includes them so
        editor-sized requests don't collide with native-resolution or thumbnail
        entries.
        """
        seq = next(self._counter)
        self._pool.start(_FrameTask(
            self._svc, path, seconds, seq, self,
            max_dim=max_dim, jpeg_quality=jpeg_quality,
        ))
        return seq

    def shutdown(self, wait_ms: int = 2000) -> None:
        """Wait for pending tasks to finish (or up to wait_ms)."""
        self._pool.clear()
        self._pool.waitForDone(wait_ms)
