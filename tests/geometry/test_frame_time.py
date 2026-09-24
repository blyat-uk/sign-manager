"""Tests for frame ↔ seconds conversion and snap helpers."""

import pytest

from sign_manager.geometry.frame_time import (
    frame_to_seconds,
    seconds_to_frame,
    snap_to_centisecond,
    snap_to_frame,
)


def test_snap_to_centisecond_rounds_to_two_decimals():
    assert snap_to_centisecond(1.234) == 1.23
    assert snap_to_centisecond(1.236) == 1.24
    assert snap_to_centisecond(0.0) == 0.0


def test_snap_to_centisecond_preserves_exact_values():
    assert snap_to_centisecond(1.23) == 1.23
    assert snap_to_centisecond(100.00) == 100.00


def test_snap_to_frame_at_30fps_lands_on_centisecond_boundary():
    # 30fps: frame N is at N/30 seconds. Frame 13 = 0.4333... -> 0.43.
    assert snap_to_frame(0.43333, 30.0) == 0.43
    assert snap_to_frame(0.45, 30.0) == 0.47   # nearest frame is 14 (0.4666... -> 0.47)


def test_snap_to_frame_at_24fps():
    # 24fps: frame 12 = 0.5 exactly.
    assert snap_to_frame(0.5, 24.0) == 0.5
    assert snap_to_frame(0.52, 24.0) == 0.5    # nearest frame is still 12


def test_snap_to_frame_with_fps_zero_falls_back_to_centisecond():
    assert snap_to_frame(1.234, 0.0) == 1.23
    assert snap_to_frame(1.234, -1.0) == 1.23


def test_seconds_to_frame_basic():
    assert seconds_to_frame(0.0, 30.0) == 0
    assert seconds_to_frame(1.0, 30.0) == 30
    assert seconds_to_frame(0.5, 24.0) == 12


def test_seconds_to_frame_with_zero_fps_returns_zero():
    assert seconds_to_frame(5.0, 0.0) == 0


def test_frame_to_seconds_round_trip_via_snap():
    assert frame_to_seconds(30, 30.0) == 1.0
    assert frame_to_seconds(13, 30.0) == 0.43   # 0.4333 -> snap to centisecond


def test_frame_to_seconds_with_zero_fps_returns_zero():
    assert frame_to_seconds(100, 0.0) == 0.0
