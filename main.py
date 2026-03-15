#!/usr/bin/env python3
import sys

from PyQt6.QtWidgets import QApplication, QMessageBox


def _check_mpv() -> bool:
    """Verify that python-mpv and libmpv are available."""
    try:
        import mpv
        # Try creating a temporary instance to verify libmpv is loadable
        player = mpv.MPV(vo="null", idle="yes")
        player.terminate()
        return True
    except Exception:
        return False


def main():
    app = QApplication(sys.argv)

    if not _check_mpv():
        QMessageBox.critical(
            None,
            "Missing Dependency",
            "mpv is required but not available.\n\n"
            "Install python-mpv:\n"
            "  pip install mpv\n\n"
            "Install libmpv:\n"
            "  Arch: pacman -S mpv\n"
            "  Ubuntu: apt install libmpv-dev\n"
            "  macOS: brew install mpv",
        )
        sys.exit(1)

    from main_window import MainWindow

    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
