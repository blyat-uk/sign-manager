"""``--self-test``: the CI hook for packaged apps.

Imports every ``sign_manager`` module, resolves and loads the external tools,
writes a JSON report (the Windows GUI stub has no stdout, hence ``--report``)
and returns the process exit code.
"""

from __future__ import annotations

import importlib
import json
import os
import pkgutil
import platform
import subprocess
import sys
import traceback
from pathlib import Path

from sign_manager.version import __version__


def _check_tool(path: str | None) -> dict:
    from sign_manager.services.proc import hidden_child

    if not path:
        return {"ok": False, "error": "not found"}
    try:
        result = subprocess.run(
            [path, "-version"], capture_output=True, text=True, timeout=60,
            **hidden_child(),
        )
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "path": path, "error": str(e)}
    first = (result.stdout or "").splitlines()[:1]
    return {
        "ok": result.returncode == 0,
        "path": path,
        "exit_code": result.returncode,
        "version": first[0] if first else "",
    }


def _check_libmpv() -> dict:
    try:
        import mpv

        player = mpv.MPV(vo="null", ao="null")
        try:
            version = player.mpv_version
        finally:
            player.terminate()
        return {"ok": True, "version": version, "file": getattr(mpv.backend, "_name", "")}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _import_all() -> dict:
    import sign_manager

    imported: list[str] = []
    failed: dict[str, str] = {}
    for info in pkgutil.walk_packages(sign_manager.__path__, "sign_manager."):
        try:
            importlib.import_module(info.name)
            imported.append(info.name)
        except Exception:
            failed[info.name] = traceback.format_exc(limit=3)
    return {"ok": not failed and bool(imported), "count": len(imported), "failed": failed}


def run(report: str | None = None) -> int:
    # The packaged Linux smoke test runs in a container with no display.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PyQt6.QtWidgets import QApplication

    from sign_manager.services import runtime_deps

    results: dict = {
        "version": __version__,
        "python": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "executable": sys.executable,
    }
    try:
        tools = runtime_deps.resolve()
        results["tools"] = {
            "ffmpeg": tools.ffmpeg, "ffprobe": tools.ffprobe, "libmpv": tools.libmpv,
            "missing": tools.missing, "runtime_dir": str(runtime_deps.runtime_dir()),
        }
        runtime_deps.prepare_libmpv(tools.libmpv)
        _app = QApplication.instance() or QApplication([sys.argv[0]])
        results["modules"] = _import_all()
        results["libmpv"] = _check_libmpv()
        results["ffmpeg"] = _check_tool(tools.ffmpeg)
        results["ffprobe"] = _check_tool(tools.ffprobe)
        ok = (
            tools.complete
            and results["modules"]["ok"]
            and results["libmpv"]["ok"]
            and results["ffmpeg"]["ok"]
            and results["ffprobe"]["ok"]
        )
    except Exception:
        results["error"] = traceback.format_exc()
        ok = False
    results["ok"] = bool(ok)

    text = json.dumps(results, indent=2, default=str)
    if report:
        Path(report).write_text(text, encoding="utf-8")
    try:
        print(text)
        print("SELF-TEST", "PASSED" if ok else "FAILED")
    except Exception:
        pass
    return 0 if ok else 1
