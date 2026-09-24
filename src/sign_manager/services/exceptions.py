"""Typed exceptions for the video service layer."""


class VideoServiceError(Exception):
    """Base class for all video service errors."""


class FFmpegNotFoundError(VideoServiceError):
    """ffmpeg or ffprobe binary is not available on PATH."""


class VideoFormatError(VideoServiceError):
    """The video file could not be parsed (unrecognized format, corrupt, etc.)."""


class FrameExtractionError(VideoServiceError):
    """A frame extraction request failed."""


class FrameExtractionTimeoutError(FrameExtractionError):
    """A frame extraction subprocess exceeded its timeout."""
