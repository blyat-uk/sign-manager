#!/usr/bin/env python3
import shutil
import sys

from PyQt6.QtWidgets import QApplication, QMessageBox


def _check_dependencies() -> list[str]:
    """Return a list of missing runtime dependencies."""
    missing: list[str] = []

    try:
        import mpv
        player = mpv.MPV(vo="null", idle="yes")
        player.terminate()
    except Exception:
        missing.append("libmpv")

    if shutil.which("ffmpeg") is None:
        missing.append("ffmpeg")
    if shutil.which("ffprobe") is None:
        missing.append("ffprobe")

    return missing


def _missing_dependencies_message(missing: list[str]) -> str:
    lines = ["The following required dependencies are missing:", ""]
    lines += [f"  \u2022 {dep}" for dep in missing]
    lines += [
        "",
        "Install them via your system package manager:",
        "",
        "  Arch:    sudo pacman -S mpv ffmpeg",
        "  Ubuntu:  sudo apt install libmpv2 ffmpeg",
        "  Fedora:  sudo dnf install mpv-libs ffmpeg",
        "  macOS:   brew install mpv ffmpeg",
        "",
        "Windows: install mpv and ffmpeg and make sure",
        "         libmpv-2.dll, ffmpeg.exe, and ffprobe.exe",
        "         are available on your PATH.",
    ]
    return "\n".join(lines)


def main():
    app = QApplication(sys.argv)

    missing = _check_dependencies()
    if missing:
        QMessageBox.critical(
            None,
            "Missing Dependencies",
            _missing_dependencies_message(missing),
        )
        sys.exit(1)

    from main_window import MainWindow

    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
