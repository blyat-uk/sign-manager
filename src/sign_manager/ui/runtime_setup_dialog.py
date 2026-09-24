"""First-run dialogs for the external tools (libmpv, ffmpeg, ffprobe).

``RuntimeSetupDialog`` offers the download on hosts that have one;
``MissingDepsDialog`` explains how to install the tools by hand.
"""

from __future__ import annotations

import enum
import threading
from pathlib import Path

from PyQt6.QtCore import QObject, QThread, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar, QPushButton,
    QVBoxLayout, QWidget,
)

from sign_manager.identity import APP_NAME
from sign_manager.services import runtime_deps, runtime_download
from sign_manager.services.runtime_download import Artifact, Cancelled, DownloadError
from sign_manager.ui.theme import PrimaryButton, Tokens

_DIALOG_QSS = f"""
QDialog {{
    background: {Tokens.bg_surface};
}}
QLabel {{
    color: {Tokens.text_primary};
    font-size: {Tokens.text_base}px;
    background: transparent;
}}
QLabel#title {{
    color: {Tokens.text_emphasis};
    font-size: {Tokens.text_lg + 2}px;
    font-weight: 700;
}}
QLabel#error {{
    color: {Tokens.danger};
}}
QLabel#muted {{
    color: {Tokens.text_muted};
}}
QPushButton#secondary {{
    background: {Tokens.bg_raised};
    color: {Tokens.text_primary};
    border: 1px solid {Tokens.border_strong};
    border-radius: {Tokens.r_md - 1}px;
    padding: 0 {Tokens.sp_3}px;
    font-size: 12px;
    min-height: 28px;
}}
QPushButton#secondary:hover {{
    background: {Tokens.bg_hover};
}}
QProgressBar {{
    background: {Tokens.bg_deepest};
    border: 1px solid {Tokens.border};
    border-radius: {Tokens.r_sm}px;
    color: {Tokens.text_primary};
    text-align: center;
    min-height: 18px;
}}
QProgressBar::chunk {{
    background: {Tokens.accent_deep};
    border-radius: {Tokens.r_sm}px;
}}
QPlainTextEdit {{
    background: {Tokens.bg_deepest};
    color: {Tokens.text_primary};
    border: 1px solid {Tokens.border};
    border-radius: {Tokens.r_sm}px;
    font-family: monospace;
    font-size: {Tokens.text_mono + 1}px;
}}
"""


def _secondary(text: str) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName("secondary")
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


def _label(text: str, name: str = "") -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    if name:
        label.setObjectName(name)
    return label


class _DownloadWorker(QObject):
    """Runs ``runtime_download.install`` on a worker thread."""

    progress = pyqtSignal(int, int)
    finished = pyqtSignal()
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, artifact: Artifact, runtime: Path) -> None:
        super().__init__()
        self._artifact = artifact
        self._runtime = runtime
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    @pyqtSlot()
    def run(self) -> None:
        try:
            runtime_download.install(
                self._artifact, self._runtime,
                progress=self.progress.emit, cancelled=self._cancel.is_set,
            )
        except Cancelled:
            self.cancelled.emit()
        except DownloadError as e:
            self.failed.emit(str(e))
        except Exception as e:  # never leave the dialog stuck
            self.failed.emit(f"Unexpected error: {e}")
        else:
            self.finished.emit()


class RuntimeSetupDialog(QDialog):
    """Offers to download libmpv + ffmpeg into the per-user runtime dir."""

    class Outcome(enum.Enum):
        INSTALLED = "installed"
        MANUAL = "manual"
        QUIT = "quit"

    def __init__(self, artifact: Artifact, runtime: Path | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._artifact = artifact
        self._runtime = runtime if runtime is not None else runtime_deps.runtime_dir()
        self._thread: QThread | None = None
        self._worker: _DownloadWorker | None = None
        self.outcome = self.Outcome.QUIT

        self.setWindowTitle(f"{APP_NAME} setup")
        self.setStyleSheet(_DIALOG_QSS)
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(Tokens.sp_5, Tokens.sp_5, Tokens.sp_5, Tokens.sp_5)
        layout.setSpacing(Tokens.sp_3)
        layout.addWidget(_label("One-time setup", "title"))
        layout.addWidget(_label(
            f"{APP_NAME} plays video with mpv and reads frames with ffmpeg. "
            f"They are not bundled with the app, so it needs to download them once "
            f"(about {artifact.size_mb:.0f} MB) from the {APP_NAME} GitHub release."
        ))
        layout.addWidget(_label(f"They will be stored in:\n{self._runtime}", "muted"))

        self._progress = QProgressBar()
        self._progress.setRange(0, max(artifact.size, 1))
        self._progress.setFormat("%p%")
        self._progress.hide()
        layout.addWidget(self._progress)

        self._status = _label("", "muted")
        self._status.hide()
        layout.addWidget(self._status)

        buttons = QHBoxLayout()
        buttons.setSpacing(Tokens.sp_2)
        self._quit = _secondary("Quit")
        self._manual = _secondary("Manual instructions")
        self._cancel = _secondary("Cancel")
        self._cancel.hide()
        self._download = PrimaryButton("Download")
        buttons.addWidget(self._quit)
        buttons.addStretch(1)
        buttons.addWidget(self._manual)
        buttons.addWidget(self._cancel)
        buttons.addWidget(self._download)
        layout.addLayout(buttons)

        self._quit.clicked.connect(self._on_quit)
        self._manual.clicked.connect(self._on_manual)
        self._cancel.clicked.connect(self._on_cancel)
        self._download.clicked.connect(self._start)

    # --- actions ----------------------------------------------------------

    def _on_quit(self) -> None:
        self.outcome = self.Outcome.QUIT
        self.reject()

    def _on_manual(self) -> None:
        self.outcome = self.Outcome.MANUAL
        self.reject()

    def _on_cancel(self) -> None:
        if self._worker is not None:
            self._cancel.setEnabled(False)
            self._status.setText("Cancelling…")
            self._worker.cancel()

    def _start(self) -> None:
        self._set_busy(True)
        self._status.setObjectName("muted")
        self._restyle(self._status)
        self._status.setText("Downloading…")
        self._progress.setValue(0)

        self._thread = QThread(self)
        self._worker = _DownloadWorker(self._artifact, self._runtime)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.cancelled.connect(self._on_cancelled)
        self._thread.start()

    # --- worker callbacks -------------------------------------------------

    @pyqtSlot(int, int)
    def _on_progress(self, done: int, total: int) -> None:
        self._progress.setMaximum(max(total, 1))
        self._progress.setValue(min(done, total))
        self._status.setText(f"Downloading… {done / 1e6:.1f} of {total / 1e6:.1f} MB")

    @pyqtSlot()
    def _on_finished(self) -> None:
        self._stop_thread()
        self.outcome = self.Outcome.INSTALLED
        self.accept()

    @pyqtSlot(str)
    def _on_failed(self, message: str) -> None:
        self._stop_thread()
        self._set_busy(False)
        self._download.setText("Retry")
        self._status.setObjectName("error")
        self._restyle(self._status)
        self._status.setText(message)

    @pyqtSlot()
    def _on_cancelled(self) -> None:
        self._stop_thread()
        self._set_busy(False)
        self._progress.hide()
        self._status.setText("Download cancelled.")

    # --- helpers ------------------------------------------------------------

    def _set_busy(self, busy: bool) -> None:
        if busy:
            self._progress.show()
        self._status.show()
        self._download.setVisible(not busy)
        self._manual.setVisible(not busy)
        self._quit.setVisible(not busy)
        self._cancel.setVisible(busy)
        self._cancel.setEnabled(busy)

    @staticmethod
    def _restyle(widget: QWidget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def _stop_thread(self) -> None:
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait()
        self._thread = None
        self._worker = None

    def reject(self) -> None:
        # Escape / window close while downloading cancels first.
        if self._worker is not None:
            self._on_cancel()
            return
        super().reject()


class MissingDepsDialog(QDialog):
    """Lists the missing tools with per-OS install commands and a Copy button."""

    def __init__(
        self,
        missing: list[str],
        *,
        os_name: str | None = None,
        detail: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME}: missing dependencies")
        self.setStyleSheet(_DIALOG_QSS)
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(Tokens.sp_5, Tokens.sp_5, Tokens.sp_5, Tokens.sp_5)
        layout.setSpacing(Tokens.sp_3)
        layout.addWidget(_label("Missing dependencies", "title"))
        layout.addWidget(_label(
            f"{APP_NAME} needs these programs, which were not found: "
            f"{', '.join(missing)}. Install them, then start {APP_NAME} again."
        ))
        if detail:
            layout.addWidget(_label(detail, "error"))

        self.commands = QPlainTextEdit(runtime_deps.install_instructions(os_name))
        self.commands.setReadOnly(True)
        self.commands.setMinimumHeight(150)
        layout.addWidget(self.commands)

        buttons = QHBoxLayout()
        copy = _secondary("Copy")
        close = PrimaryButton("Quit")
        buttons.addWidget(copy)
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout.addLayout(buttons)

        copy.clicked.connect(self._copy)
        close.clicked.connect(self.reject)

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self.commands.toPlainText())
