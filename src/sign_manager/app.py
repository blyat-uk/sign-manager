"""Entry point: ``sign-manager`` / ``python -m sign_manager``.

Startup order matters:

1. Send stdout/stderr to a log file when there is no console (pythonw, a
   Finder launch), so a crash leaves a trace.
2. Handle the command-line-only modes (``--version``, ``--self-test``,
   ``--install-runtime-deps``).
3. Create the QApplication (org/app names scope QSettings and
   AppConfigLocation).
4. Carry settings over from sub-label-pos.
5. Find ffmpeg, ffprobe and libmpv, offering the first-run download where
   there is one.
6. Point python-mpv at libmpv *before* the first ``import mpv``, check that
   it loads, then open the main window.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from sign_manager.identity import APP_NAME, APP_SLUG, BUNDLE_ID, ORG_NAME
from sign_manager.services import runtime_deps
from sign_manager.version import __version__

log = logging.getLogger(__name__)

ICON_PATH = Path(__file__).parent / "resources" / "icons" / f"{APP_SLUG}.png"


def _stream_unusable(stream) -> bool:
    return stream is None or getattr(stream, "closed", False)


def redirect_std_streams(log_dir: Path | None = None) -> Path | None:
    """Point stdout/stderr at ``<data>/logs/sign-manager.log`` when they are
    None or closed, keeping the previous run as ``.1``. Returns the log path
    when it redirected.
    """
    if not (_stream_unusable(sys.stdout) or _stream_unusable(sys.stderr)):
        return None
    try:
        log_dir = log_dir or runtime_deps.data_dir() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / f"{APP_SLUG}.log"
        if path.exists():
            os.replace(path, path.with_name(path.name + ".1"))
        stream = open(path, "w", encoding="utf-8", errors="replace", buffering=1)
    except OSError:
        return None
    if _stream_unusable(sys.stdout):
        sys.stdout = stream
    if _stream_unusable(sys.stderr):
        sys.stderr = stream
    return path


def parse_args(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(prog=APP_SLUG, description=f"{APP_NAME} {__version__}")
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    parser.add_argument(
        "--self-test", action="store_true",
        help="import every module, load libmpv/ffmpeg/ffprobe and exit 0 or 1",
    )
    parser.add_argument("--report", metavar="FILE", help="with --self-test: write a JSON report")
    parser.add_argument(
        "--install-runtime-deps", action="store_true",
        help="download mpv + ffmpeg (Windows x64 / macOS arm64) and exit",
    )
    parser.add_argument(
        "--quit-after", type=float, metavar="SECONDS",
        help="close the window after SECONDS (smoke tests)",
    )
    # Anything unrecognised (e.g. Qt's -platform) goes on to QApplication.
    return parser.parse_known_args(argv)


def install_runtime_deps_cli() -> int:
    from sign_manager.services import runtime_download

    artifact = runtime_download.artifact_for_host()
    if artifact is None:
        print(
            "There is no download for this platform; install the tools with your "
            "package manager:\n\n" + runtime_deps.install_instructions()
        )
        return 1
    target = runtime_deps.runtime_dir()
    print(f"Downloading {artifact.url} ({artifact.size_mb:.1f} MB) into {target}", flush=True)
    last = [-1]

    def progress(done: int, total: int) -> None:
        pct = int(done * 100 / total) if total else 100
        if pct >= last[0] + 10 or done == total:
            last[0] = pct
            print(f"  {pct:3d}%  {done / 1e6:7.1f} / {total / 1e6:.1f} MB", flush=True)

    try:
        runtime_download.install(artifact, target, progress=progress)
    except runtime_download.DownloadError as e:
        print(f"Download failed: {e}", flush=True)
        return 1
    tools = runtime_deps.resolve()
    print(f"Installed. ffmpeg={tools.ffmpeg} ffprobe={tools.ffprobe} libmpv={tools.libmpv}")
    return 0 if tools.complete else 1


def _ensure_tools() -> runtime_deps.RuntimeTools | None:
    """Resolve the tools, running the first-run download or showing the
    missing-dependencies dialog. ``None`` means quit."""
    from sign_manager.services import runtime_download
    from sign_manager.ui.runtime_setup_dialog import MissingDepsDialog, RuntimeSetupDialog

    tools = runtime_deps.resolve()
    if tools.complete:
        return tools
    log.warning("Missing runtime tools: %s", ", ".join(tools.missing))

    artifact = runtime_download.artifact_for_host()
    if artifact is not None:
        dialog = RuntimeSetupDialog(artifact, runtime_deps.runtime_dir())
        dialog.exec()
        if dialog.outcome is RuntimeSetupDialog.Outcome.QUIT:
            return None
        tools = runtime_deps.resolve()
        if tools.complete:
            return tools

    MissingDepsDialog(tools.missing).exec()
    return None


def _load_libmpv(tools: runtime_deps.RuntimeTools) -> str | None:
    """Prepare and load libmpv; returns an error message on failure."""
    runtime_deps.prepare_libmpv(tools.libmpv)
    try:
        import mpv

        player = mpv.MPV(vo="null")
        player.terminate()
    except Exception as e:
        log.exception("libmpv failed to load from %s", tools.libmpv)
        return f"libmpv ({tools.libmpv}) could not be loaded: {e}"
    return None


def main(argv: list[str] | None = None) -> None:
    redirect_std_streams()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    argv = sys.argv[1:] if argv is None else argv
    args, qt_args = parse_args(argv)

    if args.version:
        print(f"{APP_NAME} {__version__}")
        sys.exit(0)
    if args.self_test:
        from sign_manager import self_test

        sys.exit(self_test.run(report=args.report))
    if args.install_runtime_deps:
        sys.exit(install_runtime_deps_cli())

    from PyQt6.QtCore import QTimer
    from PyQt6.QtGui import QIcon
    from PyQt6.QtWidgets import QApplication

    app = QApplication([sys.argv[0], *qt_args])
    # Identify the app so QStandardPaths.AppConfigLocation returns a clean
    # per-app config dir (and QSettings shares the same scope). Must happen
    # before any code reads QStandardPaths or AppSettings.
    app.setOrganizationName(ORG_NAME)
    app.setApplicationName(APP_SLUG)
    app.setApplicationVersion(__version__)
    app.setDesktopFileName(BUNDLE_ID)
    if ICON_PATH.is_file():
        app.setWindowIcon(QIcon(str(ICON_PATH)))

    from sign_manager.services.settings_migration import migrate_legacy_settings

    migrate_legacy_settings()

    tools = _ensure_tools()
    if tools is None:
        sys.exit(1)

    error = _load_libmpv(tools)
    if error:
        from sign_manager.ui.runtime_setup_dialog import MissingDepsDialog

        MissingDepsDialog(["libmpv"], detail=error).exec()
        sys.exit(1)

    from sign_manager.ui.main_window import MainWindow

    win = MainWindow(ffmpeg_bin=tools.ffmpeg, ffprobe_bin=tools.ffprobe)
    win.show()
    if args.quit_after is not None:
        QTimer.singleShot(int(args.quit_after * 1000), win.close)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
