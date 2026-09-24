"""Shared pytest fixtures and configuration."""

import pytest

from sign_manager.services import runtime_deps

# python-mpv looks libmpv up once, at import time; point it at the resolved
# library (the runtime dir on Windows CI, Homebrew on macOS) before any test
# imports main_window.
_tools = runtime_deps.resolve()
if _tools.libmpv:
    runtime_deps.prepare_libmpv(_tools.libmpv)


@pytest.fixture(scope="session")
def qapp():
    """Single QApplication instance shared by all Qt tests."""
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app
