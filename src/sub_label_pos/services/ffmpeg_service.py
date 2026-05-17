"""FFmpeg/ffprobe-based VideoService implementation."""

from __future__ import annotations

import json
import subprocess
from fractions import Fraction
from pathlib import Path

from PyQt6.QtGui import QImage

from sub_label_pos.services.exceptions import (
    FFmpegNotFoundError, VideoFormatError,
    FrameExtractionError, FrameExtractionTimeoutError,
)


class FFmpegVideoService:
    """VideoService that shells out to ffmpeg/ffprobe.

    Each call invokes a fresh subprocess. For high-throughput use, wrap with
    CachedVideoService (Task H4).
    """

    def __init__(
        self,
        *,
        ffmpeg_bin: str = "ffmpeg",
        ffprobe_bin: str = "ffprobe",
        timeout_seconds: float = 10.0,
    ) -> None:
        self._ffmpeg = ffmpeg_bin
        self._ffprobe = ffprobe_bin
        self._timeout = timeout_seconds

    # --- ffprobe-based metadata -----------------------------------------

    def get_dimensions(self, path: Path) -> tuple[int, int]:
        data = self._ffprobe_json(
            ["-select_streams", "v:0", "-show_entries", "stream=width,height"],
            path,
        )
        try:
            stream = data["streams"][0]
            return int(stream["width"]), int(stream["height"])
        except (KeyError, IndexError, ValueError) as e:
            raise VideoFormatError(f"could not parse dimensions for {path}") from e

    def get_duration(self, path: Path) -> float:
        data = self._ffprobe_json(["-show_entries", "format=duration"], path)
        try:
            return float(data["format"]["duration"])
        except (KeyError, ValueError) as e:
            raise VideoFormatError(f"could not parse duration for {path}") from e

    def get_fps(self, path: Path) -> float:
        data = self._ffprobe_json(
            ["-select_streams", "v:0", "-show_entries", "stream=avg_frame_rate"],
            path,
        )
        try:
            rate = data["streams"][0]["avg_frame_rate"]
            # rate is typically "num/den"
            return float(Fraction(rate))
        except (KeyError, IndexError, ValueError, ZeroDivisionError) as e:
            raise VideoFormatError(f"could not parse fps for {path}") from e

    # --- ffmpeg frame extraction ----------------------------------------

    def get_frame(
        self,
        path: Path,
        seconds: float,
        *,
        max_dim: int | None = None,
        jpeg_quality: int | None = None,
    ) -> QImage:
        # Build ffmpeg command dynamically. Defaults preserve historical
        # behaviour: lossless PNG at native resolution.
        cmd: list[str] = [
            self._ffmpeg,
            "-ss", f"{seconds:.3f}",
            "-i", str(path),
            "-frames:v", "1",
        ]

        if max_dim is not None:
            # Downscale so neither dim exceeds max_dim, preserving aspect
            # ratio. force_original_aspect_ratio=decrease guarantees we
            # never upscale (it picks the smaller of w/h scale factors).
            cmd += [
                "-vf",
                f"scale=w='min(iw,{max_dim})':h='min(ih,{max_dim})':"
                f"force_original_aspect_ratio=decrease",
            ]

        if jpeg_quality is not None:
            # MJPEG single-frame output, smaller + faster than PNG.
            cmd += [
                "-f", "image2pipe",
                "-vcodec", "mjpeg",
                "-q:v", str(jpeg_quality),
                "-",
            ]
        else:
            # Lossless PNG (historical default).
            cmd += [
                "-f", "image2pipe",
                "-vcodec", "png",
                "-",
            ]

        try:
            result = subprocess.run(
                cmd, check=True, capture_output=True, timeout=self._timeout,
            )
        except FileNotFoundError as e:
            raise FFmpegNotFoundError(
                f"ffmpeg not on PATH (looked for {self._ffmpeg!r})"
            ) from e
        except subprocess.TimeoutExpired as e:
            raise FrameExtractionTimeoutError(
                f"ffmpeg timed out extracting {path} @ {seconds:.3f}s"
            ) from e
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode("utf-8", errors="replace") if e.stderr else ""
            raise FrameExtractionError(
                f"ffmpeg failed for {path} @ {seconds:.3f}s: {stderr.strip()}"
            ) from e

        if not result.stdout:
            raise FrameExtractionError(
                f"ffmpeg returned no data for {path} @ {seconds:.3f}s"
            )

        # QImage.fromData auto-detects format from the byte signature; works
        # for both PNG and JPEG output without specifying.
        img = QImage.fromData(result.stdout)
        if img.isNull():
            fmt = "JPEG" if jpeg_quality is not None else "PNG"
            raise FrameExtractionError(
                f"failed to decode {fmt} output for {path} @ {seconds:.3f}s"
            )
        return img

    # --- private helpers -----------------------------------------------

    def _ffprobe_json(self, extra_args: list[str], path: Path) -> dict:
        cmd = [self._ffprobe, "-v", "error", *extra_args, "-of", "json", str(path)]
        try:
            result = subprocess.run(
                cmd, check=True, capture_output=True, timeout=self._timeout,
            )
        except FileNotFoundError as e:
            raise FFmpegNotFoundError(
                f"ffprobe not on PATH (looked for {self._ffprobe!r})"
            ) from e
        except subprocess.TimeoutExpired as e:
            raise FrameExtractionTimeoutError(
                f"ffprobe timed out for {path}"
            ) from e
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode("utf-8", errors="replace") if e.stderr else ""
            raise VideoFormatError(
                f"ffprobe failed for {path}: {stderr.strip()}"
            ) from e

        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as e:
            raise VideoFormatError(f"could not parse ffprobe output for {path}") from e
