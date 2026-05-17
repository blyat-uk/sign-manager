"""Tests for the app_settings module."""
from pathlib import Path

from sub_label_pos.services import app_settings
from sub_label_pos.services.app_settings import (
    AppSettings, DisplaySettings, PerfSettings, save, save_display, load,
)


def test_display_settings_defaults():
    s = DisplaySettings()
    assert s.gallery_visible is True
    assert s.sidebar_visible is True


def test_display_settings_in_app_settings_default():
    a = AppSettings()
    assert isinstance(a.display, DisplaySettings)


def test_display_settings_roundtrip_via_save(tmp_path: Path):
    """Saving and loading preserves display fields."""
    p = tmp_path / "settings.json"
    s = AppSettings(display=DisplaySettings(gallery_visible=False, sidebar_visible=True))
    save(s, p)
    s2 = load(p)
    assert s2.display.gallery_visible is False
    assert s2.display.sidebar_visible is True


def test_save_display_preserves_other_sections(tmp_path: Path):
    """save_display rewrites only the display block; perf survives."""
    p = tmp_path / "settings.json"
    original = AppSettings(
        perf=PerfSettings(thumb_max_dim=999, mpv_quality="low"),
        display=DisplaySettings(gallery_visible=True, sidebar_visible=True),
        hardware_tier="low",
    )
    save(original, p)

    # Now mutate display only
    save_display(DisplaySettings(gallery_visible=False, sidebar_visible=False), path=p)

    reloaded = load(p)
    assert reloaded.display.gallery_visible is False
    assert reloaded.display.sidebar_visible is False
    # Perf untouched
    assert reloaded.perf.thumb_max_dim == 999
    assert reloaded.perf.mpv_quality == "low"
    assert reloaded.hardware_tier == "low"


def test_legacy_settings_file_without_display_block(tmp_path: Path):
    """Files written before this change lack 'display' — should backfill from perf.gallery_enabled."""
    import json
    p = tmp_path / "settings.json"
    legacy = {
        "version": 1,
        "hardware_tier": "low",
        "detected_ram_gb": 4.0,
        "detected_cpu_cores": 2,
        "perf": {
            "thumb_max_dim": 480, "thumb_jpeg_quality": 4, "frame_cache_size": 16,
            "preload_workers": 1, "frame_queue_workers": 1, "mpv_quality": "low",
            "gallery_enabled": False, "preload_ring": 1,
        },
    }
    p.write_text(json.dumps(legacy))
    loaded = load(p)
    # Gallery follows perf.gallery_enabled when display block absent
    assert loaded.display.gallery_visible is False
    assert loaded.display.sidebar_visible is True
