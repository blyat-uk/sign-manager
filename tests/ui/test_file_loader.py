"""Tests for FileLoader."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QEventLoop, QTimer

from sign_manager.services.exceptions import VideoServiceError
from sign_manager.ui.controllers.file_loader import FileLoader, VideoFilePair


class _OkSvc:
    def get_dimensions(self, p):
        return (1920, 1080)

    def get_duration(self, p):
        return 12.5

    def get_fps(self, p):
        return 30.0

    def get_frame(self, p, s):
        return "frame"


class _PartialFailSvc:
    """Fails dimensions/duration but succeeds for fps."""

    def get_dimensions(self, p):
        raise VideoServiceError("nope")

    def get_duration(self, p):
        raise VideoServiceError("nope")

    def get_fps(self, p):
        return 24.0

    def get_frame(self, p, s):
        return None


def _spin(ms: int = 500) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def test_find_ass_sidecar_returns_existing(qapp, tmp_path):
    video = tmp_path / "video.mkv"
    video.touch()
    ass = tmp_path / "video.ass"
    ass.write_text("[Script Info]\n", encoding="utf-8")
    assert FileLoader.find_ass_sidecar(video) == ass


def test_find_ass_sidecar_returns_none_if_missing(qapp, tmp_path):
    video = tmp_path / "video.mkv"
    video.touch()
    assert FileLoader.find_ass_sidecar(video) is None


def test_find_ass_sidecar_picks_language_suffixed_sidecar(qapp, tmp_path):
    video = tmp_path / "movie.mp4"
    video.touch()
    # No exact sidecar; only a language-suffixed one exists.
    suffixed = tmp_path / "movie.en.ass"
    suffixed.write_text("[Script Info]\n", encoding="utf-8")
    assert FileLoader.find_ass_sidecar(video) == suffixed


def test_load_video_emits_loaded_with_metadata(qapp, tmp_path):
    video = tmp_path / "video.mkv"
    video.touch()
    received: list[VideoFilePair] = []
    loader = FileLoader(_OkSvc())
    loader.loaded.connect(lambda pair: received.append(pair))
    loader.load_video(video)
    _spin(1000)
    loader.shutdown()
    assert received, "loaded signal not emitted"
    pair = received[0]
    assert pair.video_path == video
    assert pair.width == 1920
    assert pair.height == 1080
    assert pair.duration == 12.5
    assert pair.fps == 30.0
    assert pair.ass is None
    assert pair.ass_path is None


def test_load_video_tolerates_partial_metadata_failure(qapp, tmp_path):
    video = tmp_path / "v.mkv"
    video.touch()
    received: list[VideoFilePair] = []
    loader = FileLoader(_PartialFailSvc())
    loader.loaded.connect(lambda pair: received.append(pair))
    loader.load_video(video)
    _spin(1000)
    loader.shutdown()
    assert received, "loaded signal not emitted"
    pair = received[0]
    # fps succeeded; dimensions + duration failed and were swallowed.
    assert pair.fps == 24.0
    assert pair.width is None
    assert pair.height is None
    assert pair.duration is None


def test_load_video_with_real_ass_sidecar(qapp, tmp_path):
    """When a parseable sidecar exists, the AssFile should be populated."""
    fixtures = Path(__file__).resolve().parents[1] / "fixtures" / "sample.ass"
    if not fixtures.exists():
        # Skip if the project doesn't carry a sample fixture.
        import pytest as _pytest
        _pytest.skip(f"no fixture at {fixtures}")
    video = tmp_path / "sample.mkv"
    video.touch()
    ass_path = tmp_path / "sample.ass"
    ass_path.write_bytes(fixtures.read_bytes())

    received: list[VideoFilePair] = []
    loader = FileLoader(_OkSvc())
    loader.loaded.connect(lambda pair: received.append(pair))
    loader.load_video(video)
    _spin(1000)
    loader.shutdown()
    assert received, "loaded signal not emitted"
    pair = received[0]
    assert pair.ass is not None
    assert pair.ass_path == ass_path


def test_find_ass_sidecar_uses_subs_dir_when_provided(qapp, tmp_path):
    """When subs_dir is given, look in that directory using the video's stem."""
    videos = tmp_path / "videos"
    subs = tmp_path / "subs"
    videos.mkdir()
    subs.mkdir()
    video = videos / "show_s01e01.mkv"
    video.touch()
    # No sidecar next to the video.
    assert FileLoader.find_ass_sidecar(video) is None
    # Sidecar lives in the subs dir.
    ass = subs / "show_s01e01.ass"
    ass.write_text("[Script Info]\n", encoding="utf-8")
    assert FileLoader.find_ass_sidecar(video, subs_dir=subs) == ass


def test_find_ass_sidecar_subs_dir_picks_language_suffixed(qapp, tmp_path):
    """Language-suffixed sidecars in subs_dir are matched, same as in legacy dir."""
    videos = tmp_path / "videos"
    subs = tmp_path / "subs"
    videos.mkdir()
    subs.mkdir()
    video = videos / "movie.mp4"
    video.touch()
    suffixed = subs / "movie.en.ass"
    suffixed.write_text("[Script Info]\n", encoding="utf-8")
    assert FileLoader.find_ass_sidecar(video, subs_dir=subs) == suffixed


def test_find_ass_sidecar_subs_dir_overrides_local_sidecar(qapp, tmp_path):
    """The subs_dir override is explicit: a sidecar next to the video is ignored."""
    videos = tmp_path / "videos"
    subs = tmp_path / "subs"
    videos.mkdir()
    subs.mkdir()
    video = videos / "movie.mp4"
    video.touch()
    # Sidecar next to the video — would be picked up without subs_dir.
    (videos / "movie.ass").write_text("[Script Info]\n", encoding="utf-8")
    # Empty subs_dir.
    assert FileLoader.find_ass_sidecar(video, subs_dir=subs) is None


def test_find_ass_sidecar_subs_dir_missing_returns_none(qapp, tmp_path):
    """A non-existent subs_dir returns None rather than raising."""
    video = tmp_path / "video.mkv"
    video.touch()
    missing = tmp_path / "does_not_exist"
    assert FileLoader.find_ass_sidecar(video, subs_dir=missing) is None
