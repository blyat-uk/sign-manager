"""Tests for the ffmpeg/ffprobe/libmpv resolver."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from sign_manager.services import proc, runtime_deps
from sign_manager.services.runtime_deps import RuntimeTools

posix_only = pytest.mark.skipif(sys.platform == "win32", reason=":-separated paths")


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return path


def _no_which(name, path=None):  # noqa: ARG001
    return None


def _no_lib(name):  # noqa: ARG001
    return None


# --- data_dir -------------------------------------------------------------

def test_data_dir_windows_uses_localappdata():
    env = {"LOCALAPPDATA": r"C:\Users\u\AppData\Local", "HOME": "/h"}
    assert runtime_deps.data_dir(env, "win") == Path(r"C:\Users\u\AppData\Local") / "sign-manager"


def test_data_dir_mac():
    assert runtime_deps.data_dir({"HOME": "/Users/u"}, "mac") == Path(
        "/Users/u/Library/Application Support/sign-manager"
    )


def test_data_dir_linux_xdg():
    env = {"HOME": "/home/u", "XDG_DATA_HOME": "/xdg"}
    assert runtime_deps.data_dir(env, "linux") == Path("/xdg/sign-manager")


def test_data_dir_linux_default():
    assert runtime_deps.data_dir({"HOME": "/home/u"}, "linux") == Path(
        "/home/u/.local/share/sign-manager"
    )


@pytest.mark.parametrize("os_name", ["win", "mac", "linux"])
def test_data_dir_override_wins(os_name):
    env = {"HOME": "/h", "LOCALAPPDATA": "/l", "XDG_DATA_HOME": "/x", "SIGN_MANAGER_DATA_DIR": "/o"}
    assert runtime_deps.data_dir(env, os_name) == Path("/o")


def test_runtime_dir_is_under_data_dir():
    env = {"SIGN_MANAGER_DATA_DIR": "/o"}
    assert runtime_deps.runtime_dir(env, "linux") == Path("/o/runtime")


# --- host helpers ---------------------------------------------------------

@pytest.mark.parametrize("machine,expected", [
    ("AMD64", "x86_64"), ("x86_64", "x86_64"), ("arm64", "arm64"), ("aarch64", "arm64"),
])
def test_host_arch_normalises(machine, expected):
    assert runtime_deps.host_arch(machine) == expected


def test_downloadable_hosts():
    assert runtime_deps.DOWNLOADABLE_HOSTS == {("win", "x86_64"), ("mac", "arm64")}


# --- RuntimeTools ---------------------------------------------------------

def test_runtime_tools_missing_and_complete():
    assert RuntimeTools("f", "p", "m").complete
    tools = RuntimeTools(None, "p", None)
    assert tools.missing == ["libmpv", "ffmpeg"]
    assert not tools.complete


# --- resolve ----------------------------------------------------------------

def test_resolve_prefers_runtime_dir_on_windows(tmp_path):
    runtime = tmp_path / "runtime"
    for name in ("ffmpeg.exe", "ffprobe.exe", "libmpv-2.dll"):
        _touch(runtime / name)
    path_dir = tmp_path / "bin"
    _touch(path_dir / "libmpv-2.dll")
    env = {"SIGN_MANAGER_DATA_DIR": str(tmp_path), "PATH": str(path_dir)}

    tools = runtime_deps.resolve(
        env=env, os_name="win", which=lambda n, path=None: str(path_dir / f"{n}.exe"),
        find_library=_no_lib,
    )
    assert tools == RuntimeTools(
        ffmpeg=str(runtime / "ffmpeg.exe"),
        ffprobe=str(runtime / "ffprobe.exe"),
        libmpv=str(runtime / "libmpv-2.dll"),
    )


def test_resolve_falls_back_to_path(tmp_path):
    path_dir = tmp_path / "bin"
    _touch(path_dir / "mpv-2.dll")
    env = {"SIGN_MANAGER_DATA_DIR": str(tmp_path / "data"), "PATH": str(path_dir)}
    seen = []

    def which(name, path=None):
        seen.append((name, path))
        return str(path_dir / f"{name}.exe")

    tools = runtime_deps.resolve(env=env, os_name="win", which=which, find_library=_no_lib)
    assert tools.ffmpeg == str(path_dir / "ffmpeg.exe")
    assert tools.ffprobe == str(path_dir / "ffprobe.exe")
    assert tools.libmpv == str(path_dir / "mpv-2.dll")
    assert seen[0] == ("ffmpeg", str(path_dir))


def test_resolve_mac_uses_homebrew_when_not_on_path(tmp_path, monkeypatch):
    brew_bin = tmp_path / "opt" / "homebrew" / "bin"
    brew_lib = tmp_path / "opt" / "homebrew" / "lib"
    _touch(brew_bin / "ffmpeg")
    _touch(brew_bin / "ffprobe")
    _touch(brew_lib / "libmpv.dylib")
    monkeypatch.setattr(runtime_deps, "HOMEBREW_BIN_DIRS", (str(brew_bin),))
    monkeypatch.setattr(runtime_deps, "HOMEBREW_LIB_DIRS", (str(brew_lib),))
    env = {"SIGN_MANAGER_DATA_DIR": str(tmp_path / "data"), "PATH": "/usr/bin"}

    tools = runtime_deps.resolve(env=env, os_name="mac", which=_no_which, find_library=_no_lib)
    assert tools == RuntimeTools(
        ffmpeg=str(brew_bin / "ffmpeg"),
        ffprobe=str(brew_bin / "ffprobe"),
        libmpv=str(brew_lib / "libmpv.dylib"),
    )


def test_resolve_mac_runtime_dir_first(tmp_path, monkeypatch):
    runtime = tmp_path / "data" / "runtime"
    for name in ("ffmpeg", "ffprobe", "libmpv.dylib"):
        _touch(runtime / name)
    brew_lib = tmp_path / "brew"
    _touch(brew_lib / "libmpv.dylib")
    monkeypatch.setattr(runtime_deps, "HOMEBREW_LIB_DIRS", (str(brew_lib),))
    env = {"SIGN_MANAGER_DATA_DIR": str(tmp_path / "data")}
    tools = runtime_deps.resolve(env=env, os_name="mac", which=_no_which, find_library=_no_lib)
    assert tools.libmpv == str(runtime / "libmpv.dylib")
    assert tools.ffmpeg == str(runtime / "ffmpeg")


def test_resolve_linux_uses_find_library(tmp_path):
    env = {"SIGN_MANAGER_DATA_DIR": str(tmp_path), "PATH": "/usr/bin"}
    asked = []

    def find_library(name):
        asked.append(name)
        return "libmpv.so.2"

    tools = runtime_deps.resolve(
        env=env, os_name="linux", which=lambda n, path=None: f"/usr/bin/{n}",
        find_library=find_library,
    )
    assert tools == RuntimeTools("/usr/bin/ffmpeg", "/usr/bin/ffprobe", "libmpv.so.2")
    assert asked == ["mpv"]


def test_resolve_reports_everything_missing(tmp_path):
    env = {"SIGN_MANAGER_DATA_DIR": str(tmp_path), "PATH": ""}
    tools = runtime_deps.resolve(env=env, os_name="linux", which=_no_which, find_library=_no_lib)
    assert tools.missing == ["libmpv", "ffmpeg", "ffprobe"]


# --- prepare_libmpv -------------------------------------------------------

def test_prepare_libmpv_windows_prepends_path_and_adds_dll_dir(tmp_path):
    folder = str(tmp_path / "runtime")
    env = {"PATH": "existing"}
    added = []
    runtime_deps.prepare_libmpv(
        os.path.join(folder, "libmpv-2.dll"), env=env, os_name="win", add_dll_directory=added.append,
    )
    assert env["PATH"].split(os.pathsep) == [folder, "existing"]
    assert added == [folder]


@posix_only
def test_prepare_libmpv_mac_sets_dyld_fallback():
    env = {"DYLD_FALLBACK_LIBRARY_PATH": "/custom/lib"}
    runtime_deps.prepare_libmpv("/data/runtime/libmpv.dylib", env=env, os_name="mac")
    parts = env["DYLD_FALLBACK_LIBRARY_PATH"].split(":")
    assert parts[:4] == ["/data/runtime", "/opt/homebrew/lib", "/usr/local/lib", "/custom/lib"]
    assert parts[-3:] == [os.path.expanduser("~/lib"), "/lib", "/usr/lib"]
    assert len(parts) == len(set(parts))


@posix_only
def test_prepare_libmpv_mac_without_existing_value():
    env: dict[str, str] = {}
    runtime_deps.prepare_libmpv("/rt/libmpv.dylib", env=env, os_name="mac")
    assert env["DYLD_FALLBACK_LIBRARY_PATH"] == ":".join(
        ["/rt", "/opt/homebrew/lib", "/usr/local/lib", os.path.expanduser("~/lib"), "/lib", "/usr/lib"]
    )


def test_prepare_libmpv_linux_is_noop():
    env = {"PATH": "/usr/bin"}
    runtime_deps.prepare_libmpv("/usr/lib/libmpv.so.2", env=env, os_name="linux")
    assert env == {"PATH": "/usr/bin"}


def test_prepare_libmpv_none_is_noop():
    env = {"PATH": "x"}
    runtime_deps.prepare_libmpv(None, env=env, os_name="win")
    assert env == {"PATH": "x"}


@pytest.mark.parametrize("os_name", ["win", "mac", "linux"])
def test_install_instructions_mentions_both_tools(os_name):
    text = runtime_deps.install_instructions(os_name)
    assert "ffmpeg" in text.lower() and "mpv" in text.lower()


# --- proc.hidden_child ------------------------------------------------------

def test_hidden_child(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert proc.hidden_child() == {}
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    assert proc.hidden_child() == {"creationflags": 0x08000000}
