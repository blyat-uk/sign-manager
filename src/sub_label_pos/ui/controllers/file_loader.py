"""FileLoader — orchestrates loading a video + its ASS sidecar.

Moved out of MainWindow as part of refactor task K2. MainWindow remains the
orchestrator for higher-level concerns (preload cache, gallery rebuild, mode
switching, recent-files menu). FileLoader only owns the "load one file"
primitive: probe the video on a worker thread, look up the sidecar .ass,
parse it, and emit the resulting bundle.
"""

from __future__ import annotations

import glob
import logging
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal

from sub_label_pos.model.ass_file import AssFile
from sub_label_pos.services.exceptions import VideoServiceError
from sub_label_pos.services.video_service import VideoService

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class VideoFilePair:
    """The result of loading a video and its sidecar ASS file (if any).

    All metadata fields may be ``None`` when the underlying probe failed —
    callers should treat missing values defensively rather than assuming
    every load yields complete metadata.
    """

    video_path: Path
    ass_path: Path | None
    ass: AssFile | None
    width: int | None
    height: int | None
    duration: float | None
    fps: float | None


class FileLoader(QObject):
    """Loads a video file (and its ASS sidecar if present) on a background thread.

    Signals:
      loaded(VideoFilePair): emitted on successful load
      load_failed(Path, str): emitted when loading raises (path, error message)
    """

    loaded = pyqtSignal(object)              # VideoFilePair
    load_failed = pyqtSignal(object, str)    # path (Path), error message

    def __init__(self, video_service: VideoService) -> None:
        super().__init__()
        self._svc = video_service
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(1)  # one load at a time

    def load_video(self, video_path: Path | str) -> None:
        """Initiate a load on a worker thread.

        Accepts ``str`` for convenience (most call sites already carry the
        path as a string); it's normalised to ``Path`` for the result bundle.
        """
        path = Path(video_path) if not isinstance(video_path, Path) else video_path
        self._pool.start(_LoadTask(path, self._svc, self))

    def shutdown(self, wait_ms: int = 2000) -> None:
        self._pool.clear()
        self._pool.waitForDone(wait_ms)

    @staticmethod
    def find_ass_sidecar(video_path: Path) -> Path | None:
        """Return the path to a sidecar .ass file if present, else None.

        Looks for ``<stem>.ass`` first, then falls back to the first
        ``<stem>.*.ass`` match (mirrors the legacy MainWindow heuristic for
        language-suffixed sidecars like ``movie.en.ass``).
        """
        exact = video_path.with_suffix(".ass")
        if exact.is_file():
            return exact
        candidates = sorted(
            video_path.parent.glob(f"{glob.escape(video_path.stem)}.*.ass")
        )
        return candidates[0] if candidates else None


class _LoadTask(QRunnable):
    def __init__(self, video_path: Path, svc: VideoService, emitter: FileLoader) -> None:
        super().__init__()
        self._path = video_path
        self._svc = svc
        self._emitter = emitter

    def run(self) -> None:
        try:
            width: int | None = None
            height: int | None = None
            try:
                width, height = self._svc.get_dimensions(self._path)
            except VideoServiceError as e:
                log.warning("dimensions failed for %s: %s", self._path, e)
            duration: float | None = None
            try:
                duration = self._svc.get_duration(self._path)
            except VideoServiceError as e:
                log.warning("duration failed for %s: %s", self._path, e)
            fps: float | None = None
            try:
                fps = self._svc.get_fps(self._path)
            except VideoServiceError as e:
                log.warning("fps failed for %s: %s", self._path, e)

            ass_path = FileLoader.find_ass_sidecar(self._path)
            ass: AssFile | None = None
            if ass_path is not None:
                try:
                    ass = AssFile.from_path(ass_path)
                except Exception as e:
                    log.warning("ASS parse failed for %s: %s", ass_path, e)
                    ass = None
                    # Leave ass_path None so caller knows there's no usable ASS
                    ass_path = None

            self._emitter.loaded.emit(VideoFilePair(
                video_path=self._path, ass_path=ass_path, ass=ass,
                width=width, height=height, duration=duration, fps=fps,
            ))
        except Exception as e:
            log.exception("file load failed for %s", self._path)
            self._emitter.load_failed.emit(self._path, str(e))
