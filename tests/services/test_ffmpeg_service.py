"""Tests for FFmpegVideoService with mocked subprocess."""

import json
import subprocess
from pathlib import Path

import pytest

from sign_manager.services.exceptions import (
    FFmpegNotFoundError, VideoFormatError,
    FrameExtractionError, FrameExtractionTimeoutError,
)
from sign_manager.services.ffmpeg_service import FFmpegVideoService


# --- get_dimensions ---------------------------------------------------

def test_get_dimensions_parses_ffprobe_json(mocker):
    completed = mocker.Mock(
        returncode=0,
        stdout=b'{"streams":[{"width":1920,"height":1080}]}',
        stderr=b'',
    )
    mocker.patch("subprocess.run", return_value=completed)
    svc = FFmpegVideoService()
    assert svc.get_dimensions(Path("/x.mkv")) == (1920, 1080)


def test_get_dimensions_raises_format_error_on_garbage(mocker):
    mocker.patch(
        "subprocess.run",
        return_value=mocker.Mock(returncode=0, stdout=b'not json', stderr=b''),
    )
    with pytest.raises(VideoFormatError):
        FFmpegVideoService().get_dimensions(Path("/x.mkv"))


def test_get_dimensions_raises_ffmpeg_not_found(mocker):
    mocker.patch("subprocess.run", side_effect=FileNotFoundError)
    with pytest.raises(FFmpegNotFoundError):
        FFmpegVideoService().get_dimensions(Path("/x.mkv"))


def test_get_dimensions_raises_format_error_on_called_process_error(mocker):
    err = subprocess.CalledProcessError(returncode=1, cmd=["ffprobe"], stderr=b"bad")
    mocker.patch("subprocess.run", side_effect=err)
    with pytest.raises(VideoFormatError):
        FFmpegVideoService().get_dimensions(Path("/x.mkv"))


# --- get_duration ---------------------------------------------------

def test_get_duration_parses_ffprobe(mocker):
    mocker.patch(
        "subprocess.run",
        return_value=mocker.Mock(
            returncode=0,
            stdout=b'{"format":{"duration":"123.456"}}',
            stderr=b'',
        ),
    )
    assert FFmpegVideoService().get_duration(Path("/x.mkv")) == pytest.approx(123.456)


def test_get_duration_raises_on_bad_json(mocker):
    mocker.patch(
        "subprocess.run",
        return_value=mocker.Mock(returncode=0, stdout=b'broken', stderr=b''),
    )
    with pytest.raises(VideoFormatError):
        FFmpegVideoService().get_duration(Path("/x.mkv"))


# --- get_fps ---------------------------------------------------

def test_get_fps_parses_avg_frame_rate(mocker):
    mocker.patch(
        "subprocess.run",
        return_value=mocker.Mock(
            returncode=0,
            stdout=b'{"streams":[{"avg_frame_rate":"30000/1001"}]}',
            stderr=b'',
        ),
    )
    fps = FFmpegVideoService().get_fps(Path("/x.mkv"))
    assert fps == pytest.approx(29.97, abs=0.01)


def test_get_fps_handles_simple_integer_rate(mocker):
    mocker.patch(
        "subprocess.run",
        return_value=mocker.Mock(
            returncode=0,
            stdout=b'{"streams":[{"avg_frame_rate":"24"}]}',
            stderr=b'',
        ),
    )
    assert FFmpegVideoService().get_fps(Path("/x.mkv")) == 24.0


# --- get_frame ---------------------------------------------------

def test_get_frame_returns_qimage(mocker, tmp_path):
    """Mock subprocess to return a tiny valid PNG; get_frame should
    decode it into a QImage."""
    # Build a minimal 1x1 PNG by encoding via Qt itself, which guarantees
    # round-trippable bytes regardless of libpng version quirks.
    from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
    from PyQt6.QtGui import QImage as _QImage
    seed = _QImage(1, 1, _QImage.Format.Format_RGBA8888)
    seed.fill(0xFF000000)
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    seed.save(buf, "PNG")
    png_bytes = bytes(ba.data())
    mocker.patch(
        "subprocess.run",
        return_value=mocker.Mock(returncode=0, stdout=png_bytes, stderr=b''),
    )
    img = FFmpegVideoService().get_frame(Path("/x.mkv"), 1.0)
    # Don't import QImage in test header to keep tests light — just check shape:
    assert img is not None
    assert img.width() == 1
    assert img.height() == 1


def test_get_frame_raises_timeout(mocker):
    mocker.patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=10),
    )
    with pytest.raises(FrameExtractionTimeoutError):
        FFmpegVideoService().get_frame(Path("/x.mkv"), 1.0)


def test_get_frame_raises_extraction_error_on_empty_output(mocker):
    mocker.patch(
        "subprocess.run",
        return_value=mocker.Mock(returncode=0, stdout=b'', stderr=b''),
    )
    with pytest.raises(FrameExtractionError):
        FFmpegVideoService().get_frame(Path("/x.mkv"), 1.0)


def test_get_frame_raises_on_called_process_error(mocker):
    err = subprocess.CalledProcessError(returncode=1, cmd=["ffmpeg"], stderr=b"no such file")
    mocker.patch("subprocess.run", side_effect=err)
    with pytest.raises(FrameExtractionError):
        FFmpegVideoService().get_frame(Path("/x.mkv"), 1.0)


# --- new keyword params: max_dim + jpeg_quality -----------------------

def test_get_frame_with_max_dim_adds_scale_filter(mocker):
    captured = []
    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        return mocker.Mock(returncode=0, stdout=b'fake-image-bytes', stderr=b'')
    mocker.patch("subprocess.run", side_effect=fake_run)
    mocker.patch("PyQt6.QtGui.QImage.fromData", return_value=mocker.Mock(isNull=lambda: False))
    FFmpegVideoService().get_frame(Path("/x.mkv"), 1.0, max_dim=480)
    assert any("scale=" in arg for arg in captured[0])
    assert any("480" in arg for arg in captured[0])


def test_get_frame_with_jpeg_quality_uses_mjpeg(mocker):
    captured = []
    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        return mocker.Mock(returncode=0, stdout=b'fake-image-bytes', stderr=b'')
    mocker.patch("subprocess.run", side_effect=fake_run)
    mocker.patch("PyQt6.QtGui.QImage.fromData", return_value=mocker.Mock(isNull=lambda: False))
    FFmpegVideoService().get_frame(Path("/x.mkv"), 1.0, jpeg_quality=6)
    assert "mjpeg" in " ".join(captured[0])
    assert "6" in captured[0]


def test_get_frame_defaults_preserve_png(mocker):
    """Default call (no kwargs) must still produce lossless PNG, no scale."""
    captured = []
    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        return mocker.Mock(returncode=0, stdout=b'fake-image-bytes', stderr=b'')
    mocker.patch("subprocess.run", side_effect=fake_run)
    mocker.patch("PyQt6.QtGui.QImage.fromData", return_value=mocker.Mock(isNull=lambda: False))
    FFmpegVideoService().get_frame(Path("/x.mkv"), 1.0)
    cmd = captured[0]
    assert "png" in cmd
    assert "mjpeg" not in cmd
    assert not any("scale=" in arg for arg in cmd)


# --- configurable bin paths + timeout ---------------------------------

def test_custom_bin_paths_and_timeout(mocker):
    captured = []
    def fake_run(cmd, **kwargs):
        captured.append((cmd, kwargs))
        return mocker.Mock(returncode=0, stdout=b'{"streams":[{"width":640,"height":480}]}', stderr=b'')
    mocker.patch("subprocess.run", side_effect=fake_run)
    svc = FFmpegVideoService(
        ffmpeg_bin="/custom/ffmpeg",
        ffprobe_bin="/custom/ffprobe",
        timeout_seconds=3.0,
    )
    svc.get_dimensions(Path("/x.mkv"))
    cmd, kwargs = captured[0]
    assert cmd[0] == "/custom/ffprobe"
    assert kwargs.get("timeout") == 3.0
