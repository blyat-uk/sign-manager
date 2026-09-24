"""Smoke-test an unpacked Sign Manager runtime-dependency folder.

Meant to run on a *clean* machine (GitHub's windows-latest / macos-latest,
with no mpv or FFmpeg installed) against the extracted contents of
``sign-manager-deps-win-x86_64.zip`` or ``sign-manager-deps-mac-arm64.zip``.

Checks, in order:

1. libmpv loads with ctypes from the folder (``libmpv-2.dll`` on Windows,
   after ``os.add_dll_directory(folder)``; ``libmpv.dylib`` on macOS, which
   must resolve its dependencies purely through ``@loader_path`` - no
   ``DYLD_*`` variables are set or honoured).
2. ``mpv_client_api_version`` returns a sane version (printed as major.minor).
3. ``mpv_create`` returns a handle, ``mpv_initialize`` returns 0, and
   ``mpv_terminate_destroy`` tears it down.
4. ``ffmpeg -version`` and ``ffprobe -version`` from the folder exit 0
   (the first output line of each is printed).

Exits non-zero with a clear message on the first failure.

Usage::

    python packaging/runtime_deps/verify.py <folder>
"""

from __future__ import annotations

import argparse
import ctypes
import os
import struct
import subprocess
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"


class VerifyError(RuntimeError):
    """Raised when a check fails."""


def _pe_imports(path: Path) -> list[str]:
    """Return DLL names imported by a PE file (regular + delay-load imports).

    Used only to explain a failed LoadLibrary on Windows.
    """
    data = path.read_bytes()
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    coff = pe + 4
    n_sec, = struct.unpack_from("<H", data, coff + 2)
    opt_size, = struct.unpack_from("<H", data, coff + 16)
    opt = coff + 20
    magic, = struct.unpack_from("<H", data, opt)
    dd = opt + (112 if magic == 0x20B else 96)
    secs = [
        struct.unpack_from("<IIII", data, opt + opt_size + 40 * i + 8) for i in range(n_sec)
    ]

    def off(rva: int) -> int:
        for vsize, vaddr, rsize, rptr in secs:
            if vaddr <= rva < vaddr + max(vsize, rsize):
                return rva - vaddr + rptr
        raise ValueError(f"RVA {rva:#x} not mapped")

    def cstr(rva: int) -> str:
        o = off(rva)
        return data[o : data.index(b"\0", o)].decode("ascii", "replace")

    names: list[str] = []
    for index, size, name_off in ((1, 20, 12), (13, 32, 4)):
        rva = struct.unpack_from("<I", data, dd + 8 * index)[0]
        if not rva:
            continue
        o = off(rva)
        while data[o : o + size] != b"\0" * size:
            names.append(cstr(struct.unpack_from("<I", data, o + name_off)[0]))
            o += size
    return names


def _explain_windows_load_failure(dll: Path, folder: Path) -> str:
    """List imports of *dll* that cannot be found, to diagnose a load error."""
    missing: list[str] = []
    try:
        imports = _pe_imports(dll)
    except (OSError, ValueError, struct.error) as exc:
        return f"(could not parse imports: {exc})"
    for name in imports:
        if (folder / name).exists():
            continue
        try:
            ctypes.WinDLL(name)  # type: ignore[attr-defined]
        except OSError:
            missing.append(name)
    if not missing:
        return "(all direct imports are loadable; a transitive dependency is missing)"
    return "missing DLLs: " + ", ".join(missing)


def check_libmpv(folder: Path) -> None:
    """Load libmpv from *folder* and create/initialize/destroy a client."""
    if IS_WINDOWS:
        lib_path = folder / "libmpv-2.dll"
        os.add_dll_directory(str(folder))  # type: ignore[attr-defined]
    elif IS_MAC:
        lib_path = folder / "libmpv.dylib"
        leaked = sorted(k for k in os.environ if k.startswith("DYLD_"))
        if leaked:
            raise VerifyError(f"DYLD_* variables are set ({leaked}); unset them first")
    else:
        # Not a target platform; accept a libmpv.so for local testing.
        lib_path = folder / "libmpv.so"
        if not lib_path.exists():
            lib_path = folder / "libmpv.so.2"

    if not lib_path.is_file():
        raise VerifyError(f"{lib_path} not found")

    try:
        lib = ctypes.CDLL(str(lib_path))
    except OSError as exc:
        detail = _explain_windows_load_failure(lib_path, folder) if IS_WINDOWS else ""
        raise VerifyError(f"cannot load {lib_path}: {exc} {detail}".strip()) from exc
    print(f"loaded {lib_path.name}")

    lib.mpv_client_api_version.restype = ctypes.c_ulong
    lib.mpv_client_api_version.argtypes = []
    lib.mpv_create.restype = ctypes.c_void_p
    lib.mpv_create.argtypes = []
    lib.mpv_initialize.restype = ctypes.c_int
    lib.mpv_initialize.argtypes = [ctypes.c_void_p]
    lib.mpv_terminate_destroy.restype = None
    lib.mpv_terminate_destroy.argtypes = [ctypes.c_void_p]
    lib.mpv_set_option_string.restype = ctypes.c_int
    lib.mpv_set_option_string.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]

    version = lib.mpv_client_api_version()
    major, minor = version >> 16, version & 0xFFFF
    print(f"mpv client API version {major}.{minor}")
    if major < 2:
        raise VerifyError(f"unexpected mpv client API version {major}.{minor}")

    handle = lib.mpv_create()
    if not handle:
        raise VerifyError("mpv_create returned NULL")
    try:
        # Headless: no window, no audio device needed on CI.
        for opt, val in ((b"vo", b"null"), (b"ao", b"null"), (b"config", b"no")):
            lib.mpv_set_option_string(handle, opt, val)
        rc = lib.mpv_initialize(handle)
        if rc != 0:
            raise VerifyError(f"mpv_initialize returned {rc}")
        print("mpv_initialize OK")
    finally:
        lib.mpv_terminate_destroy(handle)
    print("mpv_terminate_destroy OK")


def check_tool(folder: Path, name: str) -> None:
    """Run ``<folder>/<name> -version`` and require exit status 0."""
    exe = folder / (name + ".exe" if IS_WINDOWS else name)
    if not exe.is_file():
        raise VerifyError(f"{exe} not found")
    env = {k: v for k, v in os.environ.items() if not k.startswith("DYLD_")}
    try:
        proc = subprocess.run(
            [str(exe), "-version"], capture_output=True, text=True, env=env, timeout=60
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VerifyError(f"cannot run {exe}: {exc}") from exc
    if proc.returncode != 0:
        raise VerifyError(
            f"{exe.name} -version exited {proc.returncode:#x}\n{proc.stderr.strip()}"
        )
    first = proc.stdout.splitlines()[0] if proc.stdout else "(no output)"
    print(f"{exe.name}: {first}")


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("folder", type=Path, help="unpacked runtime-deps folder")
    args = parser.parse_args(argv)
    folder = args.folder.resolve()
    if not folder.is_dir():
        print(f"FAIL: {folder} is not a directory", file=sys.stderr)
        return 2

    try:
        check_libmpv(folder)
        check_tool(folder, "ffmpeg")
        check_tool(folder, "ffprobe")
    except VerifyError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("OK: runtime dependencies verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
