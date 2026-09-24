"""Subprocess helpers shared by every place that shells out."""

from __future__ import annotations

import subprocess
import sys


def hidden_child() -> dict:
    """Keyword arguments for ``subprocess.run`` that keep a child console-less.

    A windowed Windows build has no console of its own, so every console
    program it starts (ffmpeg, ffprobe) would otherwise pop up a window for
    the lifetime of the call. Elsewhere this is a no-op.
    """
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}
