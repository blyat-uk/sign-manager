"""VideoService Protocol — abstraction for video frame extraction and metadata."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from PyQt6.QtGui import QImage


@runtime_checkable
class VideoService(Protocol):
    """Abstract video service.

    All implementations may raise ``VideoServiceError`` (or subclasses) on
    failure. ``get_frame`` is synchronous and may block; the caller is
    responsible for running it off the UI thread (see FrameRequestQueue).
    """

    def get_dimensions(self, path: Path) -> tuple[int, int]:
        """Return (width, height) in pixels."""
        ...

    def get_duration(self, path: Path) -> float:
        """Return total duration in seconds."""
        ...

    def get_fps(self, path: Path) -> float:
        """Return average frame rate (frames per second)."""
        ...

    def get_frame(self, path: Path, seconds: float) -> QImage:
        """Extract a single frame at the given timestamp (seconds)."""
        ...


from sub_label_pos.services.frame_cache import FrameCache


class CachedVideoService:
    """Decorator over a VideoService that caches frame extractions.

    Only ``get_frame`` is cached; metadata methods pass through.
    Failed ``get_frame`` calls are not cached.
    """

    def __init__(self, inner: VideoService, cache: FrameCache) -> None:
        self._inner = inner
        self._cache = cache

    def get_dimensions(self, path: Path) -> tuple[int, int]:
        return self._inner.get_dimensions(path)

    def get_duration(self, path: Path) -> float:
        return self._inner.get_duration(path)

    def get_fps(self, path: Path) -> float:
        return self._inner.get_fps(path)

    def get_frame(self, path: Path, seconds: float) -> QImage:
        key = (str(path), round(seconds, 3))
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        img = self._inner.get_frame(path, seconds)   # propagates exceptions
        self._cache.put(key, img)
        return img
