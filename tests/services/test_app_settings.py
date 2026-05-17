import json
from pathlib import Path

from sub_label_pos.services.app_settings import (
    AppSettings, PerfSettings, load, save, from_profile, _TIER_DEFAULTS,
)
from sub_label_pos.services.hardware_profile import HardwareProfile


def test_save_load_roundtrip(tmp_path):
    p = tmp_path / "settings.json"
    original = AppSettings(perf=PerfSettings(thumb_max_dim=800, thumb_jpeg_quality=5))
    save(original, p)
    loaded = load(p)
    assert loaded.perf.thumb_max_dim == 800
    assert loaded.perf.thumb_jpeg_quality == 5


def test_first_run_writes_settings(tmp_path):
    p = tmp_path / "settings.json"
    assert not p.exists()
    loaded = load(p)
    assert p.exists()
    assert loaded.hardware_tier in {"low", "medium", "high"}


def test_from_profile_low_tier():
    s = from_profile(HardwareProfile(total_ram_gb=4.0, cpu_cores=8, tier="low"))
    assert s.perf.thumb_max_dim == 480
    assert s.perf.preload_workers == 1   # tier-default, not capped


def test_from_profile_high_tier():
    s = from_profile(HardwareProfile(total_ram_gb=32.0, cpu_cores=16, tier="high"))
    assert s.perf.thumb_max_dim == 1080
    assert s.perf.frame_queue_workers == 3


def test_cpu_core_cap_overrides_tier():
    """High-tier RAM but only 2 cores -> workers capped at 1."""
    s = from_profile(HardwareProfile(total_ram_gb=32.0, cpu_cores=2, tier="high"))
    assert s.perf.preload_workers == 1
    assert s.perf.frame_queue_workers == 1


def test_invalid_settings_file_triggers_redetect(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text("not valid json")
    loaded = load(p)
    # Should have re-detected and overwritten
    assert json.loads(p.read_text())["version"] == 1


def test_low_tier_defaults_disable_gallery():
    s = from_profile(HardwareProfile(total_ram_gb=4.0, cpu_cores=8, tier="low"))
    assert s.perf.gallery_enabled is False
    assert s.perf.mpv_quality == "low"


def test_medium_tier_defaults_enable_gallery():
    s = from_profile(HardwareProfile(total_ram_gb=10.0, cpu_cores=8, tier="medium"))
    assert s.perf.gallery_enabled is True
    assert s.perf.mpv_quality == "high"


def test_high_tier_defaults_enable_gallery():
    s = from_profile(HardwareProfile(total_ram_gb=32.0, cpu_cores=16, tier="high"))
    assert s.perf.gallery_enabled is True
    assert s.perf.mpv_quality == "high"


def test_preload_ring_field_defaults():
    s = PerfSettings()
    assert s.preload_ring == 2


def test_tier_defaults_preload_ring():
    assert _TIER_DEFAULTS["low"].preload_ring == 1
    assert _TIER_DEFAULTS["medium"].preload_ring == 2
    assert _TIER_DEFAULTS["high"].preload_ring == 4


def test_preload_ring_roundtrips_through_save_load(tmp_path):
    p = tmp_path / "settings.json"
    original = AppSettings(perf=PerfSettings(preload_ring=7))
    save(original, p)
    loaded = load(p)
    assert loaded.perf.preload_ring == 7


def test_save_perf_preserves_other_fields(tmp_path):
    from sub_label_pos.services.app_settings import save_perf
    p = tmp_path / "settings.json"
    original = AppSettings(
        perf=PerfSettings(thumb_max_dim=800, gallery_enabled=True, mpv_quality="high"),
        hardware_tier="high",
        detected_ram_gb=32.0,
        detected_cpu_cores=16,
    )
    save(original, p)
    # Mutate perf and persist via save_perf.
    new_perf = PerfSettings(thumb_max_dim=800, gallery_enabled=False, mpv_quality="low")
    save_perf(new_perf, p)
    reloaded = load(p)
    assert reloaded.perf.gallery_enabled is False
    assert reloaded.perf.mpv_quality == "low"
    # Other fields untouched.
    assert reloaded.hardware_tier == "high"
    assert reloaded.detected_ram_gb == 32.0
    assert reloaded.detected_cpu_cores == 16
