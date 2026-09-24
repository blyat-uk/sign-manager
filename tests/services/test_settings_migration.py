"""Tests for carrying settings over from sub-label-pos."""

from __future__ import annotations

import json

from PyQt6.QtCore import QSettings

from sign_manager.services import settings_migration
from sign_manager.services.settings_migration import (
    legacy_settings_path, migrate_qsettings, migrate_settings_json,
)


def _ini(path) -> QSettings:
    return QSettings(str(path), QSettings.Format.IniFormat)


def test_legacy_path_is_sibling_of_new_app_dir(tmp_path):
    new = tmp_path / "BGPP" / "sign-manager" / "settings.json"
    assert legacy_settings_path(new) == tmp_path / "BGPP" / "sub-label-pos" / "settings.json"


def test_settings_json_is_copied_when_new_missing(tmp_path):
    new = tmp_path / "BGPP" / "sign-manager" / "settings.json"
    old = legacy_settings_path(new)
    old.parent.mkdir(parents=True)
    old.write_text(json.dumps({"hardware_tier": "high"}), encoding="utf-8")

    assert migrate_settings_json(new) is True
    assert json.loads(new.read_text(encoding="utf-8")) == {"hardware_tier": "high"}
    assert old.exists(), "the old file is left alone"


def test_settings_json_never_overwrites_new(tmp_path):
    new = tmp_path / "BGPP" / "sign-manager" / "settings.json"
    new.parent.mkdir(parents=True)
    new.write_text("new", encoding="utf-8")
    old = legacy_settings_path(new)
    old.parent.mkdir(parents=True)
    old.write_text("old", encoding="utf-8")

    assert migrate_settings_json(new) is False
    assert new.read_text(encoding="utf-8") == "new"


def test_settings_json_without_legacy_file(tmp_path):
    new = tmp_path / "BGPP" / "sign-manager" / "settings.json"
    assert migrate_settings_json(new) is False
    assert not new.exists()


def test_settings_json_never_raises(tmp_path, monkeypatch):
    new = tmp_path / "BGPP" / "sign-manager" / "settings.json"
    old = legacy_settings_path(new)
    old.parent.mkdir(parents=True)
    old.write_text("x", encoding="utf-8")

    def boom(*a, **k):
        raise PermissionError("denied")

    monkeypatch.setattr(settings_migration.shutil, "copy2", boom)
    assert migrate_settings_json(new) is False


def test_qsettings_keys_are_copied_into_empty_store(tmp_path):
    old = _ini(tmp_path / "old.ini")
    old.setValue("mpv/hwdec", "auto-safe")
    old.setValue("window/geometry", b"\x01\x02")
    old.setValue("recent/dirs", ["/a", "/b"])
    old.sync()
    new = _ini(tmp_path / "new.ini")

    assert migrate_qsettings(new, old) == 3
    reread = _ini(tmp_path / "new.ini")
    assert reread.value("mpv/hwdec") == "auto-safe"
    assert reread.value("window/geometry") == b"\x01\x02"
    assert reread.value("recent/dirs") == ["/a", "/b"]
    assert _ini(tmp_path / "old.ini").value("mpv/hwdec") == "auto-safe", "old store is left alone"


def test_qsettings_skips_non_empty_store(tmp_path):
    old = _ini(tmp_path / "old.ini")
    old.setValue("mpv/hwdec", "no")
    new = _ini(tmp_path / "new.ini")
    new.setValue("mpv/hwdec", "auto")

    assert migrate_qsettings(new, old) == 0
    assert new.value("mpv/hwdec") == "auto"


def test_qsettings_never_raises():
    class Broken:
        def allKeys(self):
            raise RuntimeError("broken store")

    assert migrate_qsettings(Broken(), Broken()) == 0
