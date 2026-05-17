"""User-facing performance settings, persisted to JSON.

Loaded once on app startup. If the file is missing, hardware detection
runs and a default settings file is written.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from PyQt6.QtCore import QStandardPaths

from sub_label_pos.services.hardware_profile import HardwareProfile, detect

log = logging.getLogger(__name__)


@dataclass
class PerfSettings:
    """All perf-relevant knobs. None values fall back to defaults at use site."""
    thumb_max_dim: int = 720
    thumb_jpeg_quality: int = 6
    frame_cache_size: int = 32
    preload_workers: int = 2
    frame_queue_workers: int = 2
    mpv_quality: str = "high"            # "low" (bilinear) or "high" (spline36 + deband)
    gallery_enabled: bool = True         # whether the bottom gallery panel is shown
    preload_ring: int = 2                # files preloaded on each side of the active file (0 = unlimited)


@dataclass
class AppSettings:
    """Root settings document."""
    perf: PerfSettings = field(default_factory=PerfSettings)
    hardware_tier: str = "medium"        # informational; perf is the source of truth
    detected_ram_gb: float = 0.0
    detected_cpu_cores: int = 0
    version: int = 1


_TIER_DEFAULTS = {
    "low":    PerfSettings(thumb_max_dim=480,  thumb_jpeg_quality=4, frame_cache_size=16, preload_workers=1, frame_queue_workers=1, mpv_quality="low",  gallery_enabled=False, preload_ring=1),
    "medium": PerfSettings(thumb_max_dim=720,  thumb_jpeg_quality=6, frame_cache_size=32, preload_workers=2, frame_queue_workers=2, mpv_quality="high", gallery_enabled=True,  preload_ring=2),
    "high":   PerfSettings(thumb_max_dim=1080, thumb_jpeg_quality=7, frame_cache_size=64, preload_workers=2, frame_queue_workers=3, mpv_quality="high", gallery_enabled=True,  preload_ring=4),
}


def from_profile(profile: HardwareProfile) -> AppSettings:
    """Build AppSettings from a fresh hardware detection."""
    perf = _TIER_DEFAULTS[profile.tier]
    # CPU-core cap: dual-core or weaker machines should never spawn >1 worker
    if profile.cpu_cores < 4:
        perf = PerfSettings(
            **{**asdict(perf), "preload_workers": 1, "frame_queue_workers": 1}
        )
    return AppSettings(
        perf=perf,
        hardware_tier=profile.tier,
        detected_ram_gb=round(profile.total_ram_gb, 2),
        detected_cpu_cores=profile.cpu_cores,
    )


def _settings_path() -> Path:
    """Platform-appropriate path for settings.json.

    Relies on ``QApplication.setOrganizationName`` and
    ``setApplicationName`` (set in ``app.main``) so that
    ``QStandardPaths.AppConfigLocation`` is already scoped per-app.
    Only the bare ``settings.json`` filename is appended.

    Fallback (no QApplication / unusual env) lands in
    ``~/.config/sub-label-pos/settings.json``.
    """
    base = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppConfigLocation
    )
    if base:
        return Path(base) / "settings.json"
    return Path(os.path.expanduser("~/.config")) / "sub-label-pos" / "settings.json"


def load(path: Path | None = None) -> AppSettings:
    """Load settings from disk; run first-run detection if missing/invalid."""
    p = path or _settings_path()
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return _from_dict(data)
        except (json.JSONDecodeError, OSError, KeyError, TypeError) as e:
            log.warning("Settings file invalid (%s); re-detecting", e)
    # First run (or corrupt file): detect and save
    profile = detect()
    settings = from_profile(profile)
    save(settings, p)
    log.info(
        "First-run hardware profile: %.1f GB RAM, %d cores -> tier=%s",
        profile.total_ram_gb, profile.cpu_cores, profile.tier,
    )
    return settings


def save(settings: AppSettings, path: Path | None = None) -> None:
    """Persist settings to disk."""
    p = path or _settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(_to_dict(settings), indent=2), encoding="utf-8")


def save_perf(perf: PerfSettings, path: Path | None = None) -> None:
    """Persist just the perf section, keeping other top-level fields intact.

    If the settings file does not yet exist we fall back to writing a
    fresh ``AppSettings`` document with the provided perf block.
    """
    p = path or _settings_path()
    if p.exists():
        try:
            settings = load(p)
        except Exception:
            settings = AppSettings(perf=perf)
    else:
        settings = AppSettings(perf=perf)
    settings.perf = perf
    save(settings, p)


def redetect(path: Path | None = None) -> AppSettings:
    """Re-run hardware detection and overwrite settings.

    Exposed for a future 'Re-detect hardware' menu item; not auto-called.
    """
    profile = detect()
    settings = from_profile(profile)
    save(settings, path)
    return settings


def _to_dict(s: AppSettings) -> dict:
    return {
        "version": s.version,
        "hardware_tier": s.hardware_tier,
        "detected_ram_gb": s.detected_ram_gb,
        "detected_cpu_cores": s.detected_cpu_cores,
        "perf": asdict(s.perf),
    }


def _from_dict(data: dict) -> AppSettings:
    perf_data = data.get("perf", {})
    perf_fields = {f.name for f in fields(PerfSettings)}
    # Drop unknown keys; use defaults for missing
    perf = PerfSettings(**{k: v for k, v in perf_data.items() if k in perf_fields})
    return AppSettings(
        perf=perf,
        hardware_tier=data.get("hardware_tier", "medium"),
        detected_ram_gb=float(data.get("detected_ram_gb", 0.0)),
        detected_cpu_cores=int(data.get("detected_cpu_cores", 0)),
        version=int(data.get("version", 1)),
    )
