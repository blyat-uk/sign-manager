"""Shared pytest fixtures and configuration."""

import pytest


@pytest.fixture(scope="session")
def qapp():
    """Single QApplication instance shared by all Qt tests."""
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app
