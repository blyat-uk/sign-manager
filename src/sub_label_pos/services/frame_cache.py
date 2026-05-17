"""LRU cache for video frames (composable; used by CachedVideoService)."""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, Hashable


class FrameCache:
    """Simple LRU cache.

    The cache is generic over key/value; the caller chooses the key shape.
    `CachedVideoService` uses `(str(path), round(seconds, 3))` as the key.
    """

    def __init__(self, max_size: int = 64) -> None:
        self._max = max_size
        self._d: OrderedDict[Hashable, Any] = OrderedDict()

    def get(self, key: Hashable) -> Any | None:
        if key not in self._d:
            return None
        self._d.move_to_end(key)
        return self._d[key]

    def put(self, key: Hashable, value: Any) -> None:
        if key in self._d:
            self._d.move_to_end(key)
        self._d[key] = value
        while len(self._d) > self._max:
            self._d.popitem(last=False)

    def clear(self) -> None:
        self._d.clear()
