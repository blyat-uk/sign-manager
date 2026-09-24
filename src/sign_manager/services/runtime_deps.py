"""Locate the external tools the app needs: ffmpeg, ffprobe and libmpv.

Pure and Qt-free so it can run before QApplication exists (and before the
first ``import mpv``). Every environment lookup is injectable for tests.

Lookup order for each tool:

1. the app's own runtime dir (filled by the first-run download),
2. ``PATH``,
3. on macOS, Homebrew's prefixes -- a Finder-launched .app does not get the
   shell's ``PATH``, so ``/opt/homebrew/bin`` is invisible to it otherwise.

On Linux libmpv comes from ``ctypes.util.find_library("mpv")``, which is
also what python-mpv itself uses there.
"""

from __future__ import annotations

import ctypes.util
import os
import platform
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, MutableMapping

from sign_manager.identity import APP_SLUG

# Hosts that get the first-run download; everyone else installs the tools
# with their package manager (Linux packages declare them as dependencies).
DOWNLOADABLE_HOSTS = frozenset({("win", "x86_64"), ("mac", "arm64")})

HOMEBREW_BIN_DIRS = ("/opt/homebrew/bin", "/usr/local/bin")
HOMEBREW_LIB_DIRS = ("/opt/homebrew/lib", "/usr/local/lib")

# dyld's own fallback list, which it only uses while DYLD_FALLBACK_LIBRARY_PATH
# is unset. Setting the variable replaces it, so we append it back.
_DYLD_DEFAULT_FALLBACK = ("~/lib", "/usr/local/lib", "/lib", "/usr/lib")

Which = Callable[..., "str | None"]
FindLibrary = Callable[[str], "str | None"]


def host_os(os_name: str | None = None) -> str:
    """``"win"``, ``"mac"`` or ``"linux"`` (anything else counts as linux)."""
    if os_name is not None:
        return os_name
    if sys.platform == "win32":
        return "win"
    if sys.platform == "darwin":
        return "mac"
    return "linux"


def host_arch(machine: str | None = None) -> str:
    """Normalised CPU architecture: ``"x86_64"``, ``"arm64"`` or as reported."""
    m = (machine if machine is not None else platform.machine()).lower()
    if m in ("amd64", "x86_64", "x64"):
        return "x86_64"
    if m in ("arm64", "aarch64"):
        return "arm64"
    return m


def data_dir(env: Mapping[str, str] | None = None, os_name: str | None = None) -> Path:
    """Per-user data folder (runtime tools, logs).

    ``$SIGN_MANAGER_DATA_DIR`` overrides the platform default.
    """
    env = os.environ if env is None else env
    override = env.get("SIGN_MANAGER_DATA_DIR")
    if override:
        return Path(override)
    home = Path(env.get("HOME") or env.get("USERPROFILE") or Path.home())
    os_name = host_os(os_name)
    if os_name == "win":
        base = env.get("LOCALAPPDATA")
        return Path(base) / APP_SLUG if base else home / "AppData" / "Local" / APP_SLUG
    if os_name == "mac":
        return home / "Library" / "Application Support" / APP_SLUG
    xdg = env.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else home / ".local" / "share") / APP_SLUG


def runtime_dir(env: Mapping[str, str] | None = None, os_name: str | None = None) -> Path:
    """Where the first-run download unpacks ffmpeg, ffprobe and libmpv."""
    return data_dir(env, os_name) / "runtime"


def exe_name(tool: str, os_name: str | None = None) -> str:
    return f"{tool}.exe" if host_os(os_name) == "win" else tool


def libmpv_names(os_name: str | None = None) -> tuple[str, ...]:
    """File names libmpv may have, in preference order."""
    os_name = host_os(os_name)
    if os_name == "win":
        return ("libmpv-2.dll", "mpv-2.dll")
    if os_name == "mac":
        # python-mpv's find_library("mpv") only accepts libmpv.dylib.
        return ("libmpv.dylib",)
    return ("libmpv.so.2", "libmpv.so")


@dataclass(frozen=True)
class RuntimeTools:
    """Absolute paths of the resolved tools (``None`` when not found).

    On Linux ``libmpv`` may be a bare soname (``libmpv.so.2``) as returned by
    ``find_library``; the dynamic loader resolves it.
    """

    ffmpeg: str | None
    ffprobe: str | None
    libmpv: str | None

    @property
    def missing(self) -> list[str]:
        return [
            name for name, value in (
                ("libmpv", self.libmpv), ("ffmpeg", self.ffmpeg), ("ffprobe", self.ffprobe),
            )
            if not value
        ]

    @property
    def complete(self) -> bool:
        return not self.missing


def _is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def _path_dirs(env: Mapping[str, str]) -> list[str]:
    return [d for d in env.get("PATH", "").split(os.pathsep) if d]


def _find_exe(tool: str, runtime: Path, env: Mapping[str, str], os_name: str, which: Which) -> str | None:
    local = runtime / exe_name(tool, os_name)
    if _is_file(local):
        return str(local)
    found = which(tool, path=env.get("PATH", ""))
    if found:
        return str(Path(found).absolute())
    if os_name == "mac":
        for d in HOMEBREW_BIN_DIRS:
            candidate = Path(d) / tool
            if _is_file(candidate):
                return str(candidate)
    return None


def _find_libmpv(
    runtime: Path, env: Mapping[str, str], os_name: str, find_library: FindLibrary,
) -> str | None:
    names = libmpv_names(os_name)
    for name in names:
        if _is_file(runtime / name):
            return str(runtime / name)
    if os_name == "linux":
        return find_library("mpv")
    search = list(_path_dirs(env))
    if os_name == "mac":
        search += HOMEBREW_LIB_DIRS
    for d in search:
        for name in names:
            candidate = Path(d) / name
            if _is_file(candidate):
                return str(candidate.absolute())
    return None


def resolve(
    *,
    env: Mapping[str, str] | None = None,
    os_name: str | None = None,
    which: Which = shutil.which,
    find_library: FindLibrary = ctypes.util.find_library,
) -> RuntimeTools:
    """Find ffmpeg, ffprobe and libmpv for this host."""
    env = os.environ if env is None else env
    os_name = host_os(os_name)
    runtime = runtime_dir(env, os_name)
    return RuntimeTools(
        ffmpeg=_find_exe("ffmpeg", runtime, env, os_name, which),
        ffprobe=_find_exe("ffprobe", runtime, env, os_name, which),
        libmpv=_find_libmpv(runtime, env, os_name, find_library),
    )


_dll_directory_handles: list[object] = []


def prepare_libmpv(
    path: str | None,
    *,
    env: MutableMapping[str, str] | None = None,
    os_name: str | None = None,
    add_dll_directory: Callable[[str], object] | None = None,
) -> None:
    """Make python-mpv's import-time lookup find ``path``. Call before ``import mpv``.

    python-mpv 1.0.8 cannot be handed a library path:

    * Windows: it calls ``find_library("mpv-2.dll" | "libmpv-2.dll")``, which
      walks ``PATH``; so the DLL's folder goes to the front of ``PATH`` and is
      registered with ``os.add_dll_directory`` for its dependencies.
    * macOS: it calls ``find_library("mpv")``, which goes through
      ``ctypes.macholib.dyld`` and reads ``DYLD_FALLBACK_LIBRARY_PATH`` from
      ``os.environ``; so the folder goes there, followed by Homebrew's lib
      dirs, any existing value and dyld's defaults (setting the variable
      replaces them).
    * Linux: ``find_library`` already found it; nothing to do.
    """
    if not path:
        return
    env = os.environ if env is None else env
    os_name = host_os(os_name)
    folder = os.path.dirname(path)
    if not folder:
        return
    if os_name == "win":
        env["PATH"] = os.pathsep.join([folder, *(p for p in [env.get("PATH")] if p)])
        adder = add_dll_directory or getattr(os, "add_dll_directory", None)
        if adder is not None:
            try:
                _dll_directory_handles.append(adder(folder))
            except OSError:
                pass
    elif os_name == "mac":
        parts = [folder, *HOMEBREW_LIB_DIRS]
        existing = env.get("DYLD_FALLBACK_LIBRARY_PATH")
        if existing:
            parts += existing.split(":")
        parts += [os.path.expanduser(p) for p in _DYLD_DEFAULT_FALLBACK]
        seen: set[str] = set()
        ordered = [p for p in parts if p and not (p in seen or seen.add(p))]
        env["DYLD_FALLBACK_LIBRARY_PATH"] = ":".join(ordered)


def install_instructions(os_name: str | None = None) -> str:
    """How to install the tools by hand, for the missing-dependencies dialog."""
    os_name = host_os(os_name)
    if os_name == "win":
        return (
            "Windows (winget):\n"
            "  winget install --id Gyan.FFmpeg.Shared\n"
            "  Then download libmpv-2.dll (mpv-dev-x86_64-*.7z from\n"
            "  https://github.com/shinchiro/mpv-winbuild-cmake/releases)\n"
            f"  and put it, ffmpeg.exe and ffprobe.exe in\n"
            f"  %LOCALAPPDATA%\\{APP_SLUG}\\runtime\n"
            "  or in a folder on your PATH."
        )
    if os_name == "mac":
        return "macOS (Homebrew):\n  brew install mpv ffmpeg"
    return (
        "Debian / Ubuntu:\n  sudo apt install libmpv2 ffmpeg\n\n"
        "Fedora (RPM Fusion ffmpeg recommended for HEVC):\n  sudo dnf install mpv-libs ffmpeg\n\n"
        "Arch:\n  sudo pacman -S mpv ffmpeg"
    )
