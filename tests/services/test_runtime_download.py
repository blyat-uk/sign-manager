"""Tests for the first-run runtime download, using file:// zips."""

from __future__ import annotations

import hashlib
import io
import os
import re
import stat
import sys
import zipfile
from pathlib import Path
from urllib.error import URLError

import pytest

from sign_manager.identity import REPO_URL
from sign_manager.services import runtime_deps, runtime_download
from sign_manager.services.runtime_download import Artifact, Cancelled, DownloadError, install

CONTENTS = ("libmpv.dylib", "ffmpeg", "ffprobe")


def _make_zip(path: Path, entries: dict[str, bytes], modes: dict[str, int] | None = None) -> Path:
    modes = modes or {}
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = (modes.get(name, 0o100644)) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)
    return path


def _artifact(zip_path: Path, *, sha: str | None = None, size: int | None = None,
              contents=CONTENTS) -> Artifact:
    data = zip_path.read_bytes()
    return Artifact(
        url=zip_path.as_uri(),
        sha256=sha or hashlib.sha256(data).hexdigest(),
        size=len(data) if size is None else size,
        contents=contents,
    )


def _good_zip(tmp_path: Path) -> Path:
    return _make_zip(
        tmp_path / "deps.zip",
        {
            "libmpv.dylib": b"lib" * 1000,
            "ffmpeg": b"ff" * 5000,
            "ffprobe": b"fp" * 5000,
            "licenses/mpv/LICENSE": b"GPL",
        },
        modes={"ffmpeg": 0o100755, "ffprobe": 0o100755},
    )


@pytest.fixture
def data(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    return d


def test_install_unpacks_and_cleans_up(tmp_path, data):
    runtime = data / "runtime"
    result = install(_artifact(_good_zip(tmp_path)), runtime)
    assert result == runtime
    assert (runtime / "ffmpeg").read_bytes() == b"ff" * 5000
    assert (runtime / "licenses" / "mpv" / "LICENSE").read_bytes() == b"GPL"
    assert not (data / "runtime.download").exists()
    assert not (data / "runtime.old").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_install_restores_unix_permissions(tmp_path, data):
    runtime = data / "runtime"
    install(_artifact(_good_zip(tmp_path)), runtime)
    assert os.stat(runtime / "ffmpeg").st_mode & stat.S_IXUSR
    assert not os.stat(runtime / "libmpv.dylib").st_mode & stat.S_IXUSR


def test_install_sends_user_agent(tmp_path, data):
    art = _artifact(_good_zip(tmp_path))
    seen = {}

    def opener(request, timeout):
        seen["ua"] = request.get_header("User-agent")
        seen["timeout"] = timeout
        return io.BytesIO(Path(_good_zip(tmp_path)).read_bytes())

    install(art, data / "runtime", opener=opener)
    assert seen["ua"] == f"sign-manager/{runtime_download.__version__}"
    assert seen["timeout"] > 0


def test_bad_hash_is_rejected(tmp_path, data):
    art = _artifact(_good_zip(tmp_path), sha="f" * 64)
    with pytest.raises(DownloadError, match="SHA-256"):
        install(art, data / "runtime")
    assert not (data / "runtime").exists()
    assert not (data / "runtime.download").exists()


def test_short_download_is_rejected(tmp_path, data):
    zip_path = _good_zip(tmp_path)
    art = _artifact(zip_path, size=zip_path.stat().st_size + 10)
    with pytest.raises(DownloadError, match="cut short"):
        install(art, data / "runtime")
    assert not (data / "runtime.download").exists()


def test_oversized_download_is_rejected(tmp_path, data):
    zip_path = _good_zip(tmp_path)
    art = _artifact(zip_path, size=zip_path.stat().st_size - 10)
    with pytest.raises(DownloadError, match="larger"):
        install(art, data / "runtime")


def test_missing_file_is_rejected(tmp_path, data):
    zip_path = _make_zip(tmp_path / "d.zip", {"ffmpeg": b"x", "ffprobe": b"y"})
    with pytest.raises(DownloadError, match="libmpv.dylib"):
        install(_artifact(zip_path), data / "runtime")
    assert not (data / "runtime").exists()


@pytest.mark.parametrize("evil", ["../evil", "a/../../evil", "/abs/evil", "C:/evil", "..\\evil"])
def test_zip_slip_is_rejected(tmp_path, data, evil):
    zip_path = _make_zip(tmp_path / "d.zip", {**dict.fromkeys(CONTENTS, b"x"), evil: b"boom"})
    with pytest.raises(DownloadError, match="unsafe"):
        install(_artifact(zip_path), data / "runtime")
    assert not (tmp_path / "evil").exists()
    assert not (data / "evil").exists()
    assert not (data / "runtime").exists()
    assert not (data / "runtime.download").exists()


def test_symlink_entries_are_rejected(tmp_path, data):
    zip_path = _make_zip(
        tmp_path / "d.zip", {**dict.fromkeys(CONTENTS, b"x"), "link": b"/etc/passwd"},
        modes={"link": 0o120777},
    )
    with pytest.raises(DownloadError, match="symlink"):
        install(_artifact(zip_path), data / "runtime")


def test_not_a_zip(tmp_path, data):
    bogus = tmp_path / "bogus.zip"
    bogus.write_bytes(b"not a zip at all" * 100)
    with pytest.raises(DownloadError, match="zip"):
        install(_artifact(bogus), data / "runtime")


def test_cancel_stops_and_cleans_up(tmp_path, data):
    runtime = data / "runtime"
    calls = []

    def cancelled():
        calls.append(1)
        return len(calls) > 1

    with pytest.raises(Cancelled):
        install(_artifact(_good_zip(tmp_path)), runtime, cancelled=cancelled)
    assert not runtime.exists()
    assert not (data / "runtime.download").exists()


def test_replaces_an_old_runtime(tmp_path, data):
    runtime = data / "runtime"
    runtime.mkdir()
    (runtime / "stale.dll").write_bytes(b"old")
    install(_artifact(_good_zip(tmp_path)), runtime)
    assert not (runtime / "stale.dll").exists()
    assert (runtime / "ffprobe").exists()
    assert not (data / "runtime.old").exists()


def test_failed_install_keeps_old_runtime(tmp_path, data):
    runtime = data / "runtime"
    runtime.mkdir()
    (runtime / "ffmpeg").write_bytes(b"old")
    with pytest.raises(DownloadError):
        install(_artifact(_good_zip(tmp_path), sha="0" * 64), runtime)
    assert (runtime / "ffmpeg").read_bytes() == b"old"


def test_progress_reports_the_artifact_size(tmp_path, data):
    art = _artifact(_good_zip(tmp_path))
    events = []
    install(art, data / "runtime", progress=lambda done, total: events.append((done, total)))
    assert events[0] == (0, art.size)
    assert events[-1] == (art.size, art.size)
    assert {total for _, total in events} == {art.size}
    assert [d for d, _ in events] == sorted(d for d, _ in events)


def test_network_error_becomes_download_error(tmp_path, data):
    def opener(request, timeout):
        raise URLError("no route to host")

    with pytest.raises(DownloadError, match="no route to host"):
        install(_artifact(_good_zip(tmp_path)), data / "runtime", opener=opener)
    assert not (data / "runtime.download").exists()


def test_error_mid_read_becomes_download_error(tmp_path, data):
    class Flaky(io.BytesIO):
        def read(self, n=-1):
            raise ConnectionResetError("reset by peer")

    with pytest.raises(DownloadError, match="reset by peer"):
        install(_artifact(_good_zip(tmp_path)), data / "runtime",
                opener=lambda r, timeout: Flaky(b""))


def test_missing_url_is_download_error(tmp_path, data):
    art = Artifact((tmp_path / "nope.zip").as_uri(), "0" * 64, 10, CONTENTS)
    with pytest.raises(DownloadError):
        install(art, data / "runtime")


def test_artifact_for_host():
    assert runtime_download.artifact_for_host("win", "AMD64") is runtime_download.MANIFEST[("win", "x86_64")]
    assert runtime_download.artifact_for_host("mac", "arm64") is runtime_download.MANIFEST[("mac", "arm64")]
    assert runtime_download.artifact_for_host("mac", "x86_64") is None
    assert runtime_download.artifact_for_host("linux", "x86_64") is None


# --- the pinned manifest ----------------------------------------------------

def test_manifest_keys_match_downloadable_hosts():
    assert set(runtime_download.MANIFEST) == set(runtime_deps.DOWNLOADABLE_HOSTS)


@pytest.mark.parametrize("key", sorted(runtime_download.MANIFEST))
def test_manifest_entries_are_pinned(key):
    art = runtime_download.MANIFEST[key]
    assert art.url.startswith(f"{REPO_URL}/releases/download/")
    assert art.url.endswith(".zip")
    assert re.fullmatch(r"[0-9a-f]{64}", art.sha256)
    assert art.sha256 != "0" * 64
    assert art.size > 1_000_000
    assert art.contents
