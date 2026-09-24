"""First-run download of libmpv + ffmpeg + ffprobe (Windows x64, macOS arm64).

Stdlib only. The archives come from this repo's own GitHub release
``runtime-deps-1`` (built by ``.github/workflows/runtime-deps.yml``), so an
upstream rename or a deleted nightly can never break a first run.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable
from urllib.error import URLError
from urllib.request import Request, urlopen

from sign_manager.identity import APP_SLUG, REPO_URL
from sign_manager.services import runtime_deps
from sign_manager.version import __version__

RELEASE_TAG = "runtime-deps-1"
_RELEASE_BASE = f"{REPO_URL}/releases/download/{RELEASE_TAG}"

_CHUNK = 1 << 16
_TIMEOUT_SECONDS = 60


class DownloadError(Exception):
    """The download, verification or unpacking failed; the message is user-facing."""


class Cancelled(Exception):
    """The user cancelled the download."""


@dataclass(frozen=True)
class Artifact:
    url: str
    sha256: str
    size: int
    contents: tuple[str, ...]

    @property
    def size_mb(self) -> float:
        return self.size / 1_000_000


MANIFEST: dict[tuple[str, str], Artifact] = {
    ("win", "x86_64"): Artifact(
        url=f"{_RELEASE_BASE}/{APP_SLUG}-deps-win-x86_64.zip",
        sha256="0" * 64,
        size=0,
        contents=("libmpv-2.dll", "ffmpeg.exe", "ffprobe.exe"),
    ),
    ("mac", "arm64"): Artifact(
        url=f"{_RELEASE_BASE}/{APP_SLUG}-deps-mac-arm64.zip",
        sha256="0" * 64,
        size=0,
        contents=("libmpv.dylib", "ffmpeg", "ffprobe"),
    ),
}


def artifact_for_host(os_name: str | None = None, arch: str | None = None) -> Artifact | None:
    """The download for this machine, or ``None`` where there is none."""
    key = (runtime_deps.host_os(os_name), runtime_deps.host_arch(arch))
    return MANIFEST.get(key)


Progress = Callable[[int, int], None]
IsCancelled = Callable[[], bool]


def install(
    artifact: Artifact,
    runtime: Path | None = None,
    *,
    progress: Progress | None = None,
    cancelled: IsCancelled | None = None,
    opener: Callable = urlopen,
) -> Path:
    """Download, verify and unpack ``artifact`` into ``runtime``.

    The zip lands in ``<runtime parent>/runtime.download/``; the unpacked
    tree only replaces ``runtime`` once it is complete, and the scratch dir
    is always removed. Returns ``runtime``.
    """
    runtime = Path(runtime) if runtime is not None else runtime_deps.runtime_dir()
    scratch = runtime.parent / "runtime.download"
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True)
    try:
        archive = scratch / "deps.zip"
        _download(artifact, archive, progress, cancelled, opener)
        staged = scratch / "runtime"
        _extract(archive, staged)
        missing = [name for name in artifact.contents if not (staged / name).is_file()]
        if missing:
            raise DownloadError(f"The download is missing {', '.join(missing)}.")
        if cancelled is not None and cancelled():
            raise Cancelled()
        _swap(staged, runtime)
        return runtime
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _download(
    artifact: Artifact,
    dest: Path,
    progress: Progress | None,
    cancelled: IsCancelled | None,
    opener: Callable,
) -> None:
    request = Request(artifact.url, headers={"User-Agent": f"{APP_SLUG}/{__version__}"})
    digest = hashlib.sha256()
    done = 0
    try:
        with opener(request, timeout=_TIMEOUT_SECONDS) as response, open(dest, "wb") as out:
            if progress is not None:
                progress(0, artifact.size)
            while True:
                if cancelled is not None and cancelled():
                    raise Cancelled()
                chunk = response.read(_CHUNK)
                if not chunk:
                    break
                done += len(chunk)
                if done > artifact.size:
                    raise DownloadError(
                        f"The download is larger than expected ({artifact.size} bytes)."
                    )
                digest.update(chunk)
                out.write(chunk)
                if progress is not None:
                    progress(done, artifact.size)
    except (Cancelled, DownloadError):
        raise
    except (URLError, OSError, ValueError) as e:
        reason = getattr(e, "reason", None) or e
        raise DownloadError(f"Could not download {artifact.url}: {reason}") from e
    if done != artifact.size:
        raise DownloadError(
            f"The download was cut short ({done} of {artifact.size} bytes)."
        )
    if digest.hexdigest() != artifact.sha256.lower():
        raise DownloadError("The download is corrupt (SHA-256 mismatch).")


def _extract(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True)
    root = dest.resolve()
    try:
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                target = _safe_target(root, info.filename)
                mode = (info.external_attr >> 16) & 0xFFFF
                if stat.S_ISLNK(mode):
                    raise DownloadError(f"The archive contains a symlink: {info.filename}")
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out, _CHUNK)
                perms = stat.S_IMODE(mode)
                if perms and os.name != "nt":
                    os.chmod(target, perms | stat.S_IRUSR | stat.S_IWUSR)
    except zipfile.BadZipFile as e:
        raise DownloadError(f"The download is not a valid zip: {e}") from e
    except OSError as e:
        raise DownloadError(f"Could not unpack the download: {e}") from e


def _safe_target(root: Path, name: str) -> Path:
    """Map a zip entry to a path under ``root``, rejecting zip-slip entries."""
    normalised = name.replace("\\", "/")
    pure = PurePosixPath(normalised)
    if (
        not normalised
        or pure.is_absolute()
        or ".." in pure.parts
        or (pure.parts and ":" in pure.parts[0])
    ):
        raise DownloadError(f"The archive contains an unsafe path: {name}")
    target = (root / Path(*pure.parts)).resolve()
    if target != root and root not in target.parents:
        raise DownloadError(f"The archive contains an unsafe path: {name}")
    return target


def _swap(staged: Path, runtime: Path) -> None:
    """Atomically replace ``runtime`` with ``staged``; the old tree is deleted."""
    old = runtime.parent / "runtime.old"
    shutil.rmtree(old, ignore_errors=True)
    try:
        if runtime.exists():
            os.replace(runtime, old)
        os.replace(staged, runtime)
    except OSError as e:
        if old.exists() and not runtime.exists():
            os.replace(old, runtime)
        raise DownloadError(f"Could not install into {runtime}: {e}") from e
    shutil.rmtree(old, ignore_errors=True)
