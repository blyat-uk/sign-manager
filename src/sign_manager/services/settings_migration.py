"""Carry settings over from the app's previous name (sub-label-pos).

Runs once at startup after QApplication has its org/app names. It only ever
copies: the old files and keys are left alone, and nothing here raises.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from PyQt6.QtCore import QSettings

from sign_manager.identity import APP_SLUG, LEGACY_QSETTINGS_SCOPE, LEGACY_SLUG, ORG_NAME
from sign_manager.services import app_settings

log = logging.getLogger(__name__)


def legacy_settings_path(new_path: Path) -> Path:
    """``<AppConfigLocation parent>/sub-label-pos/settings.json``."""
    return new_path.parent.parent / LEGACY_SLUG / new_path.name


def migrate_settings_json(new_path: Path | None = None) -> bool:
    """Copy the old settings.json when the new one doesn't exist yet."""
    try:
        new = new_path or app_settings._settings_path()
        old = legacy_settings_path(new)
        if new.exists() or not old.is_file():
            return False
        new.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(old, new)
        log.info("Copied settings from %s", old)
        return True
    except Exception:
        log.exception("Could not migrate settings.json")
        return False


def migrate_qsettings(
    new: QSettings | None = None, old: QSettings | None = None,
) -> int:
    """Copy every key from the old QSettings store into an empty new one.

    Returns the number of keys copied.
    """
    try:
        new = new if new is not None else QSettings(ORG_NAME, APP_SLUG)
        old = old if old is not None else QSettings(*LEGACY_QSETTINGS_SCOPE)
        if new.allKeys():
            return 0
        keys = old.allKeys()
        for key in keys:
            new.setValue(key, old.value(key))
        new.sync()
        if keys:
            log.info("Copied %d QSettings keys from %s", len(keys), old.fileName())
        return len(keys)
    except Exception:
        log.exception("Could not migrate QSettings")
        return 0


def migrate_legacy_settings() -> None:
    migrate_settings_json()
    migrate_qsettings()
