"""Hardware detection for tier selection on first run."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Literal

from sign_manager.services.proc import hidden_child

log = logging.getLogger(__name__)

Tier = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class HardwareProfile:
    total_ram_gb: float
    cpu_cores: int
    tier: Tier


def _total_ram_bytes() -> int | None:
    """Best-effort total RAM. Returns None if it can't be determined."""
    # /proc/meminfo on Linux
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return kb * 1024
    except FileNotFoundError:
        pass
    # sysctl on macOS
    try:
        import subprocess
        out = subprocess.run(
            ["sysctl", "-n", "hw.memsize"],
            capture_output=True, text=True, timeout=2, check=True,
            **hidden_child(),
        ).stdout.strip()
        return int(out)
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError):
        pass
    # GlobalMemoryStatusEx on Windows
    try:
        import ctypes
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_uint32),
                ("dwMemoryLoad", ctypes.c_uint32),
                ("ullTotalPhys", ctypes.c_uint64),
                ("ullAvailPhys", ctypes.c_uint64),
                ("ullTotalPageFile", ctypes.c_uint64),
                ("ullAvailPageFile", ctypes.c_uint64),
                ("ullTotalVirtual", ctypes.c_uint64),
                ("ullAvailVirtual", ctypes.c_uint64),
                ("sullAvailExtendedVirtual", ctypes.c_uint64),
            ]
        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        return stat.ullTotalPhys
    except (AttributeError, OSError):
        pass
    return None


def _pick_tier(ram_gb: float) -> Tier:
    if ram_gb <= 6.0:
        return "low"
    if ram_gb <= 16.0:
        return "medium"
    return "high"


def detect() -> HardwareProfile:
    """Detect the current machine's hardware and pick a tier.

    Defensive: if RAM detection fails, assume low tier to be safe.
    """
    ram_bytes = _total_ram_bytes()
    if ram_bytes is None:
        log.warning("Could not detect RAM; assuming low tier")
        ram_gb = 4.0
    else:
        ram_gb = ram_bytes / (1024 ** 3)
    cores = os.cpu_count() or 1
    tier = _pick_tier(ram_gb)
    return HardwareProfile(total_ram_gb=ram_gb, cpu_cores=cores, tier=tier)
