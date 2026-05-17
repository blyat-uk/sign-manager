"""Background folder preload — loads video metadata + first frame for each file.

This is the minimal, generic preload primitive: per-field failures are non-fatal
and surface as ``None`` on the result plus an entry in ``errors``. Callers that
need richer per-file work (ASS parsing, thumbnail generation, etc.) layer it on
top of ``FilePreloadTask``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from sub_label_pos.services.exceptions import VideoServiceError
from sub_label_pos.services.video_service import VideoService

log = logging.getLogger(__name__)


@dataclass
class PreloadResult:
    """The outcome of preloading one file.

    Per-field ``None`` means "could not be retrieved" — caller decides whether
    that's a hard failure or a degraded-but-usable state.
    """

    path: Path
    duration: float | None = None
    dimensions: tuple[int, int] | None = None
    fps: float | None = None
    first_frame: Any | None = None  # QImage; typed as Any to avoid Qt import in signature
    errors: list[str] = field(default_factory=list)


class PreloadSignals(QObject):
    """Signals emitted by ``FilePreloadTask``.

    Lives on its own class because ``QRunnable`` doesn't inherit ``QObject``
    and therefore can't host signals directly.
    """

    completed = pyqtSignal(object)  # PreloadResult


class FilePreloadTask(QRunnable):
    """Preload metadata + first frame for one file.

    Survives partial failure: any field that couldn't be retrieved is left
    as ``None`` on the result, with an error message appended to ``errors``.
    A single failure never aborts the larger batch.
    """

    def __init__(
        self,
        path: Path,
        video_service: VideoService,
        signals: PreloadSignals,
    ) -> None:
        super().__init__()
        self._path = path
        self._svc = video_service
        self._signals = signals
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        result = PreloadResult(path=self._path)
        if self._cancelled:
            return

        for label, op, setter in (
            (
                "duration",
                lambda: self._svc.get_duration(self._path),
                lambda v: setattr(result, "duration", v),
            ),
            (
                "dimensions",
                lambda: self._svc.get_dimensions(self._path),
                lambda v: setattr(result, "dimensions", v),
            ),
            (
                "fps",
                lambda: self._svc.get_fps(self._path),
                lambda v: setattr(result, "fps", v),
            ),
            (
                "first_frame",
                lambda: self._svc.get_frame(self._path, 0.0),
                lambda v: setattr(result, "first_frame", v),
            ),
        ):
            if self._cancelled:
                return
            try:
                setter(op())
            except VideoServiceError as e:
                msg = f"{label}: {e}"
                result.errors.append(msg)
                log.warning("preload %s failed for %s: %s", label, self._path, e)

        if not self._cancelled:
            self._signals.completed.emit(result)
