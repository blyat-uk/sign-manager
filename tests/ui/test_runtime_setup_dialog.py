"""Tests for the first-run setup and missing-dependencies dialogs."""

from __future__ import annotations

import hashlib
import zipfile

from PyQt6.QtWidgets import QApplication

from sign_manager.services.runtime_download import Artifact
from sign_manager.ui.runtime_setup_dialog import MissingDepsDialog, RuntimeSetupDialog


def _artifact(tmp_path, contents=("ffmpeg",)):
    zip_path = tmp_path / "deps.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("ffmpeg", b"x" * 2000)
    data = zip_path.read_bytes()
    return Artifact(zip_path.as_uri(), hashlib.sha256(data).hexdigest(), len(data), contents)


def _wait_for(dialog, qapp, timeout_ms=5000):
    from PyQt6.QtCore import QDeadlineTimer

    deadline = QDeadlineTimer(timeout_ms)
    while dialog._thread is not None and not deadline.hasExpired():
        qapp.processEvents()


def test_quit_and_manual_outcomes(qapp, tmp_path):
    dialog = RuntimeSetupDialog(_artifact(tmp_path), tmp_path / "runtime")
    dialog._quit.click()
    assert dialog.outcome is RuntimeSetupDialog.Outcome.QUIT

    dialog = RuntimeSetupDialog(_artifact(tmp_path), tmp_path / "runtime")
    dialog._manual.click()
    assert dialog.outcome is RuntimeSetupDialog.Outcome.MANUAL


def test_download_installs(qapp, tmp_path):
    runtime = tmp_path / "data" / "runtime"
    dialog = RuntimeSetupDialog(_artifact(tmp_path), runtime)
    dialog._download.click()
    _wait_for(dialog, qapp)
    assert dialog.outcome is RuntimeSetupDialog.Outcome.INSTALLED
    assert (runtime / "ffmpeg").is_file()


def test_download_failure_offers_retry(qapp, tmp_path):
    runtime = tmp_path / "data" / "runtime"
    dialog = RuntimeSetupDialog(_artifact(tmp_path, contents=("libmpv.dylib",)), runtime)
    dialog._download.click()
    _wait_for(dialog, qapp)
    assert dialog.outcome is RuntimeSetupDialog.Outcome.QUIT
    assert dialog._download.text() == "Retry"
    assert not dialog._download.isHidden()
    assert "libmpv.dylib" in dialog._status.text()


def test_missing_deps_copy(qapp):
    dialog = MissingDepsDialog(["libmpv", "ffmpeg"], os_name="linux")
    dialog._copy()
    assert "apt install libmpv2 ffmpeg" in QApplication.clipboard().text()
