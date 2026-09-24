"""Build the macOS arm64 runtime-dependency zip for Sign Manager.

Produces ``sign-manager-deps-mac-arm64.zip`` from Homebrew's ``mpv`` and
``ffmpeg`` bottles. Run on an Apple Silicon machine after::

    brew install mpv ffmpeg

What the script does:

1. Takes ``$(brew --prefix mpv)/lib/libmpv.2.dylib`` (stored as
   ``libmpv.dylib``) and ``$(brew --prefix ffmpeg)/bin/{ffmpeg,ffprobe}``,
   resolving symlinks to the real files.
2. Computes the transitive closure of every non-system dylib they load
   (``otool -L``; "system" means under ``/usr/lib/`` or ``/System/``).
   ``@rpath/`` references are resolved through the LC_RPATH entries of the
   loading file and its loaders (``@loader_path``/``@executable_path`` in
   rpaths are expanded); ``@loader_path/`` references are resolved relative to
   the loading file. Files are deduplicated by real path and named by the
   basename of their install name.
3. Copies everything flat into one folder, makes it user-writable, sets each
   dylib's id to ``@loader_path/<name>``, rewrites every non-system reference
   to ``@loader_path/<name>``, deletes all LC_RPATHs, and finally ad-hoc
   re-signs each file (signing must come last, since install_name_tool
   invalidates signatures).
4. Fails if ``otool -L`` on any output file still mentions ``/opt/homebrew``,
   ``/usr/local`` or ``@rpath``, or if any LC_RPATH remains.
5. Adds license files from the Homebrew Cellar of every formula involved under
   ``licenses/<formula>/`` plus a ``licenses/README.txt`` listing versions and
   sources from ``brew info --json=v2``.
6. Writes a zip that preserves Unix permission bits (so ``ffmpeg``/``ffprobe``
   stay executable) and contains no symlinks.

Only the Python standard library and the Xcode command-line tools
(``otool``, ``install_name_tool``, ``codesign``) plus ``brew`` are used.

Usage::

    python packaging/runtime_deps/build_mac.py [--out dist] [--work DIR]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

ZIP_NAME = "sign-manager-deps-mac-arm64.zip"
ZIP_DATE_TIME = (1980, 1, 1, 0, 0, 0)

SYSTEM_PREFIXES = ("/usr/lib/", "/System/")
FORBIDDEN_IN_OTOOL = ("/opt/homebrew", "/usr/local", "@rpath")
LICENSE_PREFIXES = ("license", "licence", "copying", "copyright", "notice")

_OTOOL_L_RE = re.compile(r"^\s+(.+?) \(compatibility version [^)]*\)\s*$")
_RPATH_RE = re.compile(r"^\s*path (.+) \(offset \d+\)\s*$")


class BuildError(RuntimeError):
    """Raised for any condition that must abort the build."""


# ---------------------------------------------------------------------------
# Tool wrappers
# ---------------------------------------------------------------------------


def run(cmd: list[str]) -> str:
    """Run *cmd*, returning stdout; raise BuildError with stderr on failure."""
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise BuildError(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stderr.strip()}"
        )
    return proc.stdout


def is_system(path: str) -> bool:
    """True if *path* is an OS-provided library that must not be bundled."""
    return path.startswith(SYSTEM_PREFIXES)


def dylib_id(path: Path) -> str | None:
    """Return the LC_ID_DYLIB install name of *path*, or None for executables."""
    lines = [ln.strip() for ln in run(["otool", "-D", str(path)]).splitlines()[1:]]
    lines = [ln for ln in lines if ln]
    return lines[0] if lines else None


def linked_libs(path: Path, own_id: str | None) -> list[str]:
    """Return the load-command references of *path* as written in the file.

    Parses ``otool -L``: the first line (``<file>:``) and any other header
    lines ending in ``:`` are skipped, as is the file's own install name,
    which ``otool -L`` lists first for dylibs.
    """
    refs: list[str] = []
    for line in run(["otool", "-L", str(path)]).splitlines():
        if line.rstrip().endswith(":"):
            continue
        m = _OTOOL_L_RE.match(line)
        if not m:
            continue
        ref = m.group(1)
        if ref == own_id or ref in refs:
            continue
        refs.append(ref)
    return refs


def rpaths(path: Path) -> list[str]:
    """Return the raw LC_RPATH strings of *path*, in load-command order."""
    out: list[str] = []
    in_rpath = False
    for line in run(["otool", "-l", str(path)]).splitlines():
        stripped = line.strip()
        if stripped.startswith("cmd "):
            in_rpath = stripped == "cmd LC_RPATH"
            continue
        if in_rpath:
            m = _RPATH_RE.match(line)
            if m:
                out.append(m.group(1))
                in_rpath = False
    return out


# ---------------------------------------------------------------------------
# Dependency closure
# ---------------------------------------------------------------------------


@dataclass
class MachOFile:
    """One file of the closure."""

    real: Path  # resolved real path of the source file
    name: str  # flat name in the output folder
    is_dylib: bool
    refs: list[str] = field(default_factory=list)  # raw non-self references
    # raw reference -> output name (bundled) or absolute system path
    rewrites: dict[str, str] = field(default_factory=dict)


def _expand(token_path: str, loader_dirs: list[Path], exe_dir: Path) -> list[Path]:
    """Expand @loader_path / @executable_path in *token_path* to candidates."""
    if token_path.startswith("@loader_path"):
        rest = token_path[len("@loader_path") :].lstrip("/")
        return [d / rest for d in loader_dirs]
    if token_path.startswith("@executable_path"):
        rest = token_path[len("@executable_path") :].lstrip("/")
        return [exe_dir / rest]
    return [Path(token_path)]


def resolve_ref(
    ref: str,
    loader_dirs: list[Path],
    rpath_dirs: list[Path],
    exe_dir: Path,
    who: str,
) -> tuple[Path | None, str | None]:
    """Resolve one load reference.

    Returns ``(real_path, None)`` for a bundled dependency, or
    ``(None, system_path)`` for a system library (``system_path`` is the
    absolute path the reference should point at).
    """
    if is_system(ref):
        return None, ref
    if ref.startswith("@rpath/"):
        rest = ref[len("@rpath/") :]
        system_fallback: str | None = None
        for d in rpath_dirs:
            cand = d / rest
            if is_system(str(d) + "/"):
                # System libraries on macOS 11+ live in the dyld shared cache
                # and usually do not exist on disk, so we cannot stat them.
                if cand.exists():
                    return None, str(cand)
                system_fallback = system_fallback or str(cand)
                continue
            if cand.exists():
                return cand.resolve(), None
        if system_fallback:
            return None, system_fallback
        raise BuildError(
            f"{who}: cannot resolve {ref} via rpaths {[str(d) for d in rpath_dirs]}"
        )
    for cand in _expand(ref, loader_dirs, exe_dir):
        if cand.exists():
            real = cand.resolve()
            if is_system(str(real)):
                return None, str(real)
            return real, None
    raise BuildError(f"{who}: dependency {ref} does not exist")


def compute_closure(
    roots: list[tuple[Path, str, Path]],
) -> dict[Path, MachOFile]:
    """Walk dependencies of *roots* (``(loaded_path, out_name, exe_dir)``).

    Returns a mapping of real path -> :class:`MachOFile`.
    """
    files: dict[Path, MachOFile] = {}
    names: dict[str, Path] = {}
    # Queue entries: (loaded path, forced name or None, inherited rpath dirs, exe dir)
    queue: list[tuple[Path, str | None, list[Path], Path]] = [
        (loaded, name, [], exe_dir) for loaded, name, exe_dir in roots
    ]
    pending_rewrites: list[tuple[MachOFile, str, Path]] = []

    while queue:
        loaded, forced_name, inherited, exe_dir = queue.pop(0)
        real = loaded.resolve()
        if real in files:
            continue
        own_id = dylib_id(real)
        name = forced_name or Path(own_id or real.name).name
        if name in names and names[name] != real:
            raise BuildError(f"name clash: {name} for {names[name]} and {real}")
        names[name] = real
        mf = MachOFile(real=real, name=name, is_dylib=own_id is not None)
        mf.refs = linked_libs(real, own_id)
        files[real] = mf

        # @loader_path is the directory the image was loaded from; try both
        # the (possibly symlinked) path used to load it and its real path.
        loader_dirs = list(dict.fromkeys([loaded.parent, real.parent]))
        own_rpaths: list[Path] = []
        for rp in rpaths(real):
            own_rpaths.extend(_expand(rp, loader_dirs, exe_dir))
        # dyld searches the loading image's rpaths first, then its loaders'.
        search = list(dict.fromkeys(own_rpaths + inherited))

        for ref in mf.refs:
            dep_real, sys_path = resolve_ref(ref, loader_dirs, search, exe_dir, str(real))
            if sys_path is not None:
                if sys_path != ref:
                    mf.rewrites[ref] = sys_path
                continue
            assert dep_real is not None
            pending_rewrites.append((mf, ref, dep_real))
            if dep_real not in files:
                dep_loaded = _loaded_path(ref, loader_dirs, search, exe_dir) or dep_real
                queue.append((dep_loaded, None, search, exe_dir))

    for mf, ref, dep_real in pending_rewrites:
        mf.rewrites[ref] = files[dep_real].name
    return files


def _loaded_path(
    ref: str, loader_dirs: list[Path], rpath_dirs: list[Path], exe_dir: Path
) -> Path | None:
    """Return the non-realpath'd path a reference would load from, if any."""
    if ref.startswith("@rpath/"):
        rest = ref[len("@rpath/") :]
        for d in rpath_dirs:
            if (d / rest).exists():
                return d / rest
        return None
    for cand in _expand(ref, loader_dirs, exe_dir):
        if cand.exists():
            return cand
    return None


# ---------------------------------------------------------------------------
# Rewriting and verification
# ---------------------------------------------------------------------------


def relink(folder: Path, files: dict[Path, MachOFile]) -> None:
    """Copy, rewrite install names, drop rpaths and re-sign every file."""
    for mf in files.values():
        dst = folder / mf.name
        shutil.copyfile(mf.real, dst)  # follows symlinks; real file content
        mode = stat.S_IMODE(mf.real.stat().st_mode) | stat.S_IWUSR | stat.S_IRUSR
        os.chmod(dst, mode)

    for mf in sorted(files.values(), key=lambda m: m.name):
        dst = str(folder / mf.name)
        # Delete rpaths one at a time and re-read, so duplicate entries are
        # handled too. Doing this first also frees header space for -change.
        for _ in range(64):
            current = rpaths(Path(dst))
            if not current:
                break
            run(["install_name_tool", "-delete_rpath", current[0], dst])
        else:
            raise BuildError(f"{mf.name}: could not delete all LC_RPATHs")

        cmd = ["install_name_tool"]
        if mf.is_dylib:
            cmd += ["-id", f"@loader_path/{mf.name}"]
        for ref, target in sorted(mf.rewrites.items()):
            new = target if target.startswith("/") else f"@loader_path/{target}"
            cmd += ["-change", ref, new]
        if len(cmd) > 1:
            cmd.append(dst)
            run(cmd)

    # Signing must be the very last modification of each file.
    for mf in sorted(files.values(), key=lambda m: m.name):
        run(["codesign", "--force", "--sign", "-", str(folder / mf.name)])


def verify_folder(folder: Path, files: dict[Path, MachOFile]) -> None:
    """Fail unless every output file only references system libs or siblings."""
    present = {mf.name for mf in files.values()}
    problems: list[str] = []
    for mf in sorted(files.values(), key=lambda m: m.name):
        path = folder / mf.name
        text = run(["otool", "-L", str(path)])
        for bad in FORBIDDEN_IN_OTOOL:
            if bad in text:
                problems.append(f"{mf.name}: otool -L still mentions {bad}")
        if rpaths(path):
            problems.append(f"{mf.name}: LC_RPATH entries remain")
        own_id = dylib_id(path)
        if mf.is_dylib and own_id != f"@loader_path/{mf.name}":
            problems.append(f"{mf.name}: unexpected id {own_id}")
        for ref in linked_libs(path, own_id):
            if is_system(ref):
                continue
            if not ref.startswith("@loader_path/"):
                problems.append(f"{mf.name}: non-relocatable reference {ref}")
            elif ref[len("@loader_path/") :] not in present:
                problems.append(f"{mf.name}: references missing sibling {ref}")
        run(["codesign", "--verify", "--strict", str(path)])
    if problems:
        raise BuildError("verification failed:\n  " + "\n  ".join(problems))
    print(f"verified {len(files)} Mach-O files: only system and @loader_path references")


def smoke_test(folder: Path) -> None:
    """Run the relocated ffmpeg/ffprobe once as a basic sanity check."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("DYLD_")}
    for exe in ("ffmpeg", "ffprobe"):
        proc = subprocess.run(
            [str(folder / exe), "-hide_banner", "-version"],
            capture_output=True,
            text=True,
            env=env,
        )
        if proc.returncode != 0:
            raise BuildError(f"{exe} -version failed:\n{proc.stderr}")
        print(f"{exe}: {proc.stdout.splitlines()[0]}")


# ---------------------------------------------------------------------------
# Licenses
# ---------------------------------------------------------------------------


def formula_of(real: Path, cellar: Path) -> str | None:
    """Return the Homebrew formula owning *real*, based on its Cellar path."""
    try:
        rel = real.relative_to(cellar)
    except ValueError:
        return None
    return rel.parts[0] if rel.parts else None


def keg_of(real: Path, cellar: Path) -> Path | None:
    """Return ``<cellar>/<formula>/<version>`` containing *real*."""
    try:
        rel = real.relative_to(cellar)
    except ValueError:
        return None
    return cellar / rel.parts[0] / rel.parts[1] if len(rel.parts) >= 2 else None


def license_files(keg: Path) -> list[Path]:
    """Find license-like files at the keg root and in ``share/doc/*/``."""
    found: list[Path] = []
    dirs = [keg]
    doc = keg / "share" / "doc"
    if doc.is_dir():
        dirs += sorted(p for p in doc.iterdir() if p.is_dir())
    for d in dirs:
        for p in sorted(d.iterdir()):
            if p.is_file() and p.name.lower().startswith(LICENSE_PREFIXES):
                found.append(p)
    return found


def brew_info(formulae: list[str]) -> dict[str, dict]:
    """Return ``brew info --json=v2`` data keyed by formula name."""
    data = json.loads(run(["brew", "info", "--json=v2", *formulae]))
    return {f["name"]: f for f in data.get("formulae", [])}


def readme_text(
    info: dict[str, dict], owners: dict[str, list[str]], unowned: list[str]
) -> str:
    """Compose licenses/README.txt describing provenance and licensing."""
    lines = [
        "Sign Manager runtime dependencies - macOS arm64",
        "=" * 46,
        "",
        "Binaries are taken from Homebrew bottles (https://brew.sh) of the",
        "formulae listed below. The only changes made are to Mach-O load",
        "commands (install names rewritten to @loader_path, LC_RPATHs removed)",
        "followed by an ad-hoc code signature. Code is otherwise unmodified.",
        "",
        "libmpv.dylib is libmpv.2.dylib from the mpv formula",
        "(https://github.com/mpv-player/mpv, GPL-2.0-or-later as built).",
        "ffmpeg/ffprobe and libav*/libsw* are from the ffmpeg formula",
        "(https://ffmpeg.org/, built with --enable-gpl).",
        "",
        "Corresponding source code is available from each formula's source URL",
        "below, and Homebrew's build recipes from",
        "https://github.com/Homebrew/homebrew-core/tree/HEAD/Formula .",
        "License texts found in the Homebrew kegs are under licenses/<formula>/.",
        "",
        "Formulae:",
        "",
    ]
    for name in sorted(owners):
        f = info.get(name, {})
        installed = f.get("installed") or [{}]
        version = installed[0].get("version") or f.get("versions", {}).get("stable", "?")
        src = f.get("urls", {}).get("stable", {})
        lines += [
            f"  {name} {version}",
            f"    license:  {f.get('license') or 'unknown'}",
            f"    homepage: {f.get('homepage', '')}",
            f"    source:   {src.get('url', '')}",
        ]
        if src.get("checksum"):
            lines.append(f"    sha256:   {src['checksum']}")
        if src.get("revision"):
            lines.append(f"    revision: {src['revision']}")
        lines.append(f"    formula:  https://github.com/Homebrew/homebrew-core/blob/HEAD/"
                     f"Formula/{name[0]}/{name}.rb")
        lines.append(f"    files:    {', '.join(sorted(owners[name]))}")
        lines.append("")
    if unowned:
        lines += ["Files not attributable to a formula:", ""]
        lines += [f"  {n}" for n in unowned]
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Zip
# ---------------------------------------------------------------------------


def write_zip(out: Path, folder: Path) -> int:
    """Zip *folder* (sorted, fixed timestamps, Unix modes, no symlinks)."""
    paths = sorted(p for p in folder.rglob("*") if not p.is_dir())
    tmp = out.with_suffix(".zip.part")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for p in paths:
            if p.is_symlink():
                raise BuildError(f"refusing to store symlink {p}")
            arcname = p.relative_to(folder).as_posix()
            mode = stat.S_IMODE(p.stat().st_mode)
            info = zipfile.ZipInfo(arcname, date_time=ZIP_DATE_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3  # Unix, so external_attr carries the mode
            info.external_attr = (stat.S_IFREG | mode) << 16
            with p.open("rb") as src, zf.open(info, "w", force_zip64=True) as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
    tmp.replace(out)
    return len(paths)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    """Return the hex SHA-256 digest of *path*."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build(out_dir: Path, work: Path) -> Path:
    """Run the whole build in *work* and return the path of the finished zip."""
    for tool in ("brew", "otool", "install_name_tool", "codesign"):
        if shutil.which(tool) is None:
            raise BuildError(f"required tool not found: {tool}")

    mpv_prefix = Path(run(["brew", "--prefix", "mpv"]).strip())
    ff_prefix = Path(run(["brew", "--prefix", "ffmpeg"]).strip())
    cellar = Path(run(["brew", "--cellar"]).strip()).resolve()

    libmpv = mpv_prefix / "lib" / "libmpv.2.dylib"
    ffmpeg = ff_prefix / "bin" / "ffmpeg"
    ffprobe = ff_prefix / "bin" / "ffprobe"
    for p in (libmpv, ffmpeg, ffprobe):
        if not p.exists():
            raise BuildError(f"missing {p} - run `brew install mpv ffmpeg` first")

    roots = [
        (libmpv, "libmpv.dylib", libmpv.resolve().parent),
        (ffmpeg, "ffmpeg", ffmpeg.resolve().parent),
        (ffprobe, "ffprobe", ffprobe.resolve().parent),
    ]
    files = compute_closure(roots)
    print(f"closure: {len(files)} Mach-O files")
    for mf in sorted(files.values(), key=lambda m: m.name):
        print(f"  {mf.name:40s} <- {mf.real}")

    folder = work / "sign-manager-deps"
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)

    relink(folder, files)
    verify_folder(folder, files)
    smoke_test(folder)

    # Licenses, grouped by the formula that owns each file.
    owners: dict[str, list[str]] = {}
    kegs: dict[str, Path] = {}
    unowned: list[str] = []
    for mf in files.values():
        formula = formula_of(mf.real, cellar)
        keg = keg_of(mf.real, cellar)
        if formula is None or keg is None:
            unowned.append(mf.name)
            continue
        owners.setdefault(formula, []).append(mf.name)
        kegs[formula] = keg
    lic_root = folder / "licenses"
    lic_root.mkdir()
    for formula, keg in sorted(kegs.items()):
        found = license_files(keg)
        if not found:
            print(f"WARNING: no license file found in {keg}")
            continue
        dest = lic_root / formula
        dest.mkdir(parents=True, exist_ok=True)
        for src in found:
            rel = src.relative_to(keg).as_posix().replace("/", "_")
            target = dest / rel
            shutil.copyfile(src, target)
            os.chmod(target, 0o644)
    info = brew_info(sorted(owners))
    readme = lic_root / "README.txt"
    readme.write_text(readme_text(info, owners, sorted(unowned)), encoding="utf-8")
    os.chmod(readme, 0o644)

    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / ZIP_NAME
    count = write_zip(out, folder)
    print(f"\nzipped {count} files from {folder}")
    return out


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=Path("dist"), help="output directory")
    parser.add_argument(
        "--work",
        type=Path,
        default=None,
        help="keep the staging folder here (default: temporary directory)",
    )
    args = parser.parse_args(argv)

    if sys.platform != "darwin":
        print("ERROR: build_mac.py must run on macOS", file=sys.stderr)
        return 1
    try:
        if args.work is not None:
            out = build(args.out.resolve(), args.work.resolve())
        else:
            with tempfile.TemporaryDirectory(prefix="sm-deps-mac-") as tmp:
                out = build(args.out.resolve(), Path(tmp))
    except (BuildError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    size = out.stat().st_size
    print(f"\n{out}")
    print(f"size:   {size} bytes ({size / 1e6:.1f} MB)")
    print(f"sha256: {sha256_file(out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
