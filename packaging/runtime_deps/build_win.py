"""Build the Windows x64 runtime-dependency zip for Sign Manager.

Produces ``sign-manager-deps-win-x86_64.zip``: a *flat* zip containing

* ``libmpv-2.dll`` from shinchiro's mpv-winbuild-cmake ``mpv-dev`` package
  (baseline x86-64 build; FFmpeg is linked statically into it),
* ``ffmpeg.exe``, ``ffprobe.exe`` and the shared FFmpeg DLLs they import
  (``av*``, ``sw*``, ``postproc*``) from BtbN's FFmpeg-Builds ``gpl-shared``
  package,
* ``licenses/`` with the upstream license files and a ``README.txt`` naming
  the upstream URLs, versions and SHA-256 digests.

Every upstream download is pinned by SHA-256; a mismatch aborts the build.

Runs on Linux (the GitHub workflow uses ubuntu-24.04). Requires the ``7z``
command-line tool (``apt-get install p7zip-full``) to unpack the mpv archive.
Everything else is Python standard library.

Usage::

    python packaging/runtime_deps/build_win.py [--out dist] [--cache DIR]
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

ZIP_NAME = "sign-manager-deps-win-x86_64.zip"

# Fixed timestamp for every zip entry so rebuilding from the same inputs
# yields the same bytes.
ZIP_DATE_TIME = (1980, 1, 1, 0, 0, 0)

MPV_COMMIT = "2a4eb8067ca68ec19adf23daf8ccbb1a05afd6ed"


@dataclass(frozen=True)
class Upstream:
    """A pinned upstream download."""

    name: str
    version: str
    url: str
    sha256: str
    filename: str


MPV_DEV = Upstream(
    name="mpv (libmpv) - shinchiro/mpv-winbuild-cmake",
    version=f"20260924 (mpv git {MPV_COMMIT[:10]}), x86_64 baseline",
    url=(
        "https://github.com/shinchiro/mpv-winbuild-cmake/releases/download/"
        "20260924/mpv-dev-x86_64-20260924-git-2a4eb8067c.7z"
    ),
    sha256="50caf10ddb6644da4c205ce67dfe37e32fc3715b33c82e933d6e1962ff61eac9",
    filename="mpv-dev-x86_64-20260924-git-2a4eb8067c.7z",
)

FFMPEG = Upstream(
    name="FFmpeg - BtbN/FFmpeg-Builds (win64 gpl-shared)",
    version="n8.1.3 (autobuild-2026-09-24-14-14)",
    url=(
        "https://github.com/BtbN/FFmpeg-Builds/releases/download/"
        "autobuild-2026-09-24-14-14/ffmpeg-n8.1.3-win64-gpl-shared-8.1.zip"
    ),
    sha256="ef8310a639c2577d9f1a04c0b112c3659d3f3547a2582d5f519b5cd0c8487dcf",
    filename="ffmpeg-n8.1.3-win64-gpl-shared-8.1.zip",
)

# The mpv-dev archive ships no license text, so fetch mpv's own license files
# from the exact commit the build was made from (also pinned by SHA-256).
_MPV_RAW = f"https://raw.githubusercontent.com/mpv-player/mpv/{MPV_COMMIT}"
MPV_LICENSES = [
    Upstream(
        name="mpv Copyright",
        version=MPV_COMMIT,
        url=f"{_MPV_RAW}/Copyright",
        sha256="bfe9ee4cceabcb8ecbfadf208d04156f73d801e6a57369a5606bb8341e204a23",
        filename="mpv-Copyright",
    ),
    Upstream(
        name="mpv LICENSE.GPL",
        version=MPV_COMMIT,
        url=f"{_MPV_RAW}/LICENSE.GPL",
        sha256="edaef632cbb643e4e7a221717a6c441a4c1a7c918e6e4d56debc3d8739b233f6",
        filename="mpv-LICENSE.GPL",
    ),
    Upstream(
        name="mpv LICENSE.LGPL",
        version=MPV_COMMIT,
        url=f"{_MPV_RAW}/LICENSE.LGPL",
        sha256="72b672113d642cbb8ef5dcc76938db801983c56e50b1400ab930f1a64d6dc8d9",
        filename="mpv-LICENSE.LGPL",
    ),
]

# DLLs belonging to FFmpeg's shared build; any import matching this must be
# shipped in the zip.
FFMPEG_DLL_RE = re.compile(r"^(av[a-z]+|sw[a-z]+|postproc)-\d+\.dll$", re.IGNORECASE)

# Imports that are not part of a stock Windows install. They are not bundled
# (we ship upstream builds unmodified) but are worth flagging in the log.
NON_OS_DLLS = {"vulkan-1.dll"}


class BuildError(RuntimeError):
    """Raised for any condition that must abort the build."""


# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    """Return the hex SHA-256 digest of *path*."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(up: Upstream, cache: Path) -> Path:
    """Download *up* into *cache* (reusing a cached copy) and verify its hash.

    A cached file with the wrong digest is discarded and re-downloaded; a
    freshly downloaded file with the wrong digest raises :class:`BuildError`.
    """
    cache.mkdir(parents=True, exist_ok=True)
    dest = cache / up.filename
    if dest.exists():
        if sha256_file(dest) == up.sha256:
            print(f"cached   {up.filename}")
            return dest
        print(f"cached   {up.filename} has wrong SHA-256, re-downloading")
        dest.unlink()

    print(f"download {up.url}")
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(up.url, headers={"User-Agent": "sign-manager-build"})
    with urllib.request.urlopen(req, timeout=120) as resp, tmp.open("wb") as out:
        shutil.copyfileobj(resp, out, 1 << 20)
    actual = sha256_file(tmp)
    if actual != up.sha256:
        tmp.unlink()
        raise BuildError(
            f"SHA-256 mismatch for {up.url}\n  expected {up.sha256}\n  actual   {actual}"
        )
    tmp.replace(dest)
    return dest


# ---------------------------------------------------------------------------
# PE import parsing (stdlib only)
# ---------------------------------------------------------------------------


def pe_imports(path: Path) -> list[str]:
    """Return the DLL names imported by the PE file at *path*.

    Includes both the regular import table and the delay-load import table.
    """
    data = path.read_bytes()
    if data[:2] != b"MZ":
        raise BuildError(f"{path.name}: not a PE file")
    pe_off = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe_off : pe_off + 4] != b"PE\0\0":
        raise BuildError(f"{path.name}: bad PE signature")
    coff = pe_off + 4
    n_sections, = struct.unpack_from("<H", data, coff + 2)
    opt_size, = struct.unpack_from("<H", data, coff + 16)
    opt = coff + 20
    magic, = struct.unpack_from("<H", data, opt)
    dd_base = opt + (112 if magic == 0x20B else 96)
    n_dirs, = struct.unpack_from("<I", data, dd_base - 4)

    sections = []
    sec_base = opt + opt_size
    for i in range(n_sections):
        vsize, vaddr, rsize, rptr = struct.unpack_from("<IIII", data, sec_base + 40 * i + 8)
        sections.append((vaddr, max(vsize, rsize), rptr))

    def rva_to_off(rva: int) -> int:
        for vaddr, size, rptr in sections:
            if vaddr <= rva < vaddr + size:
                return rva - vaddr + rptr
        raise BuildError(f"{path.name}: RVA {rva:#x} outside all sections")

    def cstr(rva: int) -> str:
        off = rva_to_off(rva)
        return data[off : data.index(b"\0", off)].decode("ascii", "replace")

    def data_dir(index: int) -> tuple[int, int]:
        if index >= n_dirs:
            return 0, 0
        return struct.unpack_from("<II", data, dd_base + 8 * index)

    names: list[str] = []
    imp_rva, _ = data_dir(1)
    if imp_rva:
        off = rva_to_off(imp_rva)
        while True:
            desc = data[off : off + 20]
            if desc == b"\0" * 20:
                break
            names.append(cstr(struct.unpack_from("<I", desc, 12)[0]))
            off += 20
    delay_rva, _ = data_dir(13)
    if delay_rva:
        off = rva_to_off(delay_rva)
        while True:
            desc = data[off : off + 32]
            if desc == b"\0" * 32:
                break
            names.append(cstr(struct.unpack_from("<I", desc, 4)[0]))
            off += 32
    return names


# ---------------------------------------------------------------------------
# Build steps
# ---------------------------------------------------------------------------


def extract_libmpv(archive: Path, workdir: Path) -> Path:
    """Extract ``libmpv-2.dll`` from the mpv-dev 7z archive using ``7z``."""
    if shutil.which("7z") is None:
        raise BuildError("the `7z` command is required (apt-get install p7zip-full)")
    outdir = workdir / "mpv"
    subprocess.run(
        ["7z", "x", "-y", f"-o{outdir}", str(archive), "libmpv-2.dll"],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    dll = outdir / "libmpv-2.dll"
    if not dll.is_file():
        raise BuildError(f"libmpv-2.dll not found in {archive.name}")
    return dll


def extract_ffmpeg(archive: Path, workdir: Path) -> tuple[list[Path], Path]:
    """Extract ffmpeg/ffprobe, the FFmpeg DLLs and LICENSE from BtbN's zip.

    Returns ``(binaries, license_path)``.
    """
    outdir = workdir / "ffmpeg"
    wanted: list[Path] = []
    license_path: Path | None = None
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            parts = info.filename.split("/")
            if info.is_dir() or len(parts) < 2:
                continue
            rel = "/".join(parts[1:])  # strip "ffmpeg-n8.1.3-win64-.../"
            base = parts[-1]
            keep_bin = rel.startswith("bin/") and (
                base.lower() in ("ffmpeg.exe", "ffprobe.exe") or FFMPEG_DLL_RE.match(base)
            )
            is_license = rel.count("/") == 0 and base.upper().startswith(
                ("LICENSE", "COPYING", "README")
            )
            if not (keep_bin or is_license):
                continue
            target = outdir / base
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
            if keep_bin:
                wanted.append(target)
            elif base.upper().startswith("LICENSE"):
                license_path = target
    names = {p.name.lower() for p in wanted}
    for required in ("ffmpeg.exe", "ffprobe.exe"):
        if required not in names:
            raise BuildError(f"{required} not found in {archive.name}")
    if license_path is None:
        raise BuildError(f"no LICENSE file found in {archive.name}")
    return wanted, license_path


def check_imports(binaries: dict[str, Path]) -> None:
    """Verify every FFmpeg DLL imported by a bundled PE is itself bundled.

    Also confirms libmpv-2.dll is self-contained (imports no FFmpeg DLLs) and
    logs any non-bundled imports that are not part of stock Windows.
    """
    bundled = {name.lower() for name in binaries}
    missing: list[str] = []
    external: set[str] = set()
    for name, path in sorted(binaries.items()):
        imports = pe_imports(path)
        for dll in imports:
            low = dll.lower()
            if low in bundled:
                continue
            if FFMPEG_DLL_RE.match(low):
                missing.append(f"{name} -> {dll}")
            else:
                external.add(low)
        if name == "libmpv-2.dll":
            ff = [d for d in imports if FFMPEG_DLL_RE.match(d)]
            if ff:
                raise BuildError(f"libmpv-2.dll is not self-contained, imports {ff}")
            print("libmpv-2.dll imports no FFmpeg DLLs (FFmpeg is linked statically)")
    if missing:
        raise BuildError("bundled binaries import missing FFmpeg DLLs:\n  " + "\n  ".join(missing))
    print("all FFmpeg DLL imports are satisfied inside the bundle")
    flagged = sorted(external & NON_OS_DLLS)
    if flagged:
        print(
            "NOTE: imports not shipped with stock Windows (provided by GPU drivers): "
            + ", ".join(flagged)
        )


def readme_text(sources: list[Upstream], bundled: list[str]) -> str:
    """Compose licenses/README.txt describing provenance and licensing."""
    lines = [
        "Sign Manager runtime dependencies - Windows x86_64",
        "=" * 50,
        "",
        "This archive contains unmodified binaries built by third parties.",
        "",
        "libmpv-2.dll is mpv's client library (https://github.com/mpv-player/mpv),",
        "built by shinchiro/mpv-winbuild-cmake with FFmpeg linked statically. This",
        "build is licensed GPL-2.0-or-later (mpv is LGPL-2.1-or-later only when",
        "built with -Dgpl=false). See licenses/mpv/.",
        "",
        "ffmpeg.exe, ffprobe.exe and the av*/sw* DLLs are FFmpeg",
        "(https://ffmpeg.org/) built by BtbN/FFmpeg-Builds in its GPL",
        "configuration (GPL-3.0-or-later as a whole). See licenses/ffmpeg/.",
        "",
        "Corresponding source code:",
        "  mpv:             https://github.com/mpv-player/mpv (commit " + MPV_COMMIT + ")",
        "  mpv build:       https://github.com/shinchiro/mpv-winbuild-cmake",
        "  FFmpeg:          https://ffmpeg.org/download.html#get-sources",
        "  FFmpeg build:    https://github.com/BtbN/FFmpeg-Builds",
        "",
        "Upstream downloads (verified by SHA-256 at build time):",
        "",
    ]
    for up in sources:
        lines += [
            f"  {up.name}",
            f"    version: {up.version}",
            f"    url:     {up.url}",
            f"    sha256:  {up.sha256}",
            "",
        ]
    lines += ["Bundled binaries:", ""]
    lines += [f"  {name}" for name in bundled]
    lines.append("")
    return "\n".join(lines)


def write_zip(out: Path, entries: dict[str, bytes | Path]) -> None:
    """Write *entries* (arcname -> bytes or file path) as a sorted zip."""
    tmp = out.with_suffix(".zip.part")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for arcname in sorted(entries):
            src = entries[arcname]
            info = zipfile.ZipInfo(arcname, date_time=ZIP_DATE_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (0o100644 << 16)
            if isinstance(src, Path):
                with src.open("rb") as fh, zf.open(info, "w", force_zip64=True) as dst:
                    shutil.copyfileobj(fh, dst, 1 << 20)
            else:
                zf.writestr(info, src)
    tmp.replace(out)


def build(out_dir: Path, cache: Path) -> Path:
    """Run the whole build and return the path of the finished zip."""
    mpv_archive = fetch(MPV_DEV, cache)
    ff_archive = fetch(FFMPEG, cache)
    mpv_license_files = [fetch(up, cache) for up in MPV_LICENSES]

    with tempfile.TemporaryDirectory(prefix="sm-deps-win-") as tmp:
        work = Path(tmp)
        libmpv = extract_libmpv(mpv_archive, work)
        ff_bins, ff_license = extract_ffmpeg(ff_archive, work)

        binaries: dict[str, Path] = {libmpv.name: libmpv}
        for p in ff_bins:
            binaries[p.name] = p
        check_imports(binaries)

        entries: dict[str, bytes | Path] = dict(binaries)
        entries["licenses/ffmpeg/" + ff_license.name] = ff_license
        for up, path in zip(MPV_LICENSES, mpv_license_files, strict=True):
            entries["licenses/mpv/" + up.filename.removeprefix("mpv-")] = path
        entries["licenses/README.txt"] = readme_text(
            [MPV_DEV, FFMPEG, *MPV_LICENSES], sorted(binaries)
        ).encode()

        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / ZIP_NAME
        write_zip(out, entries)

    print(f"\n{len(entries)} entries:")
    for name in sorted(entries):
        print(f"  {name}")
    return out


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=Path("dist"), help="output directory")
    parser.add_argument(
        "--cache",
        type=Path,
        default=None,
        help="directory for downloaded upstream archives (default: temporary)",
    )
    args = parser.parse_args(argv)

    try:
        if args.cache is not None:
            out = build(args.out, args.cache)
        else:
            with tempfile.TemporaryDirectory(prefix="sm-deps-cache-") as cache:
                out = build(args.out, Path(cache))
    except (BuildError, subprocess.CalledProcessError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    size = out.stat().st_size
    print(f"\n{out}")
    print(f"size:   {size} bytes ({size / 1e6:.1f} MB)")
    print(f"sha256: {sha256_file(out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
