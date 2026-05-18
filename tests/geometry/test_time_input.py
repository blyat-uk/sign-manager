"""Tests for parse_time_input: absolute + relative time-string parsing."""

import pytest

from sub_label_pos.geometry.time_input import parse_time_input, parse_relative_delta


# --- Absolute forms ---

def test_absolute_h_mm_ss_cc():
    assert parse_time_input("0:01:23.45", current=0.0, fps=30.0) == pytest.approx(83.47, abs=0.02)


def test_absolute_m_ss_cc():
    assert parse_time_input("1:23.45", current=0.0, fps=30.0) == pytest.approx(83.47, abs=0.02)


def test_absolute_ss_cc():
    assert parse_time_input("83.45", current=0.0, fps=30.0) == pytest.approx(83.47, abs=0.02)


def test_absolute_ss_integer():
    assert parse_time_input("83", current=0.0, fps=30.0) == pytest.approx(83.0, abs=0.02)


def test_absolute_strips_whitespace():
    assert parse_time_input("  1:23.45  ", current=0.0, fps=30.0) == pytest.approx(83.47, abs=0.02)


# --- Relative forms (evaluated against `current`) ---

def test_relative_plus_ms():
    assert parse_time_input("+250ms", current=10.0, fps=30.0) == pytest.approx(10.27, abs=0.02)


def test_relative_minus_ms():
    assert parse_time_input("-250ms", current=10.0, fps=30.0) == pytest.approx(9.73, abs=0.02)


def test_relative_plus_seconds():
    assert parse_time_input("+3s", current=10.0, fps=30.0) == pytest.approx(13.0, abs=0.02)


def test_relative_minus_seconds_fractional():
    assert parse_time_input("-1.5s", current=10.0, fps=30.0) == pytest.approx(8.5, abs=0.02)


def test_relative_plus_frames():
    # +6 frames at 30fps = +0.2s
    assert parse_time_input("+6f", current=10.0, fps=30.0) == pytest.approx(10.2, abs=0.02)


def test_relative_minus_frames():
    assert parse_time_input("-12f", current=10.0, fps=30.0) == pytest.approx(9.6, abs=0.02)


def test_relative_units_are_case_insensitive():
    assert parse_time_input("+250MS", current=0.0, fps=30.0) == pytest.approx(0.27, abs=0.02)
    assert parse_time_input("+6F", current=0.0, fps=30.0) == pytest.approx(0.2, abs=0.02)


# --- Frame snap on output ---

def test_absolute_result_is_frame_snapped():
    # 83.45s at 30fps -> nearest frame is 2504 (83.4666...) -> 83.47
    assert parse_time_input("83.45", current=0.0, fps=30.0) == 83.47


def test_relative_result_is_frame_snapped():
    # current=0, +250ms = 0.25s, at 30fps frame 8 = 0.2666 -> 0.27; frame 7 = 0.2333 -> 0.23
    # round(0.25 * 30) = 8 (banker's: 7.5 rounds to 8 in Python's round) → 0.27
    result = parse_time_input("+250ms", current=0.0, fps=30.0)
    assert result == 0.27


# --- Failure cases ---

def test_empty_string_returns_none():
    assert parse_time_input("", current=0.0, fps=30.0) is None


def test_whitespace_only_returns_none():
    assert parse_time_input("   ", current=0.0, fps=30.0) is None


def test_garbage_returns_none():
    assert parse_time_input("hello", current=0.0, fps=30.0) is None


def test_unknown_unit_returns_none():
    assert parse_time_input("+5x", current=0.0, fps=30.0) is None


def test_negative_absolute_no_sign_returns_none():
    # "-1:00.00" is not a valid relative (no unit) and not a valid absolute (negative)
    assert parse_time_input("-1:00.00", current=0.0, fps=30.0) is None


def test_partial_numeric_returns_none():
    assert parse_time_input("1::23", current=0.0, fps=30.0) is None


def test_relative_without_number_returns_none():
    assert parse_time_input("+ms", current=0.0, fps=30.0) is None


# --- fps = 0 fallback ---

def test_fps_zero_skips_frame_snap_but_keeps_centisecond():
    # No frame snap; just centisecond.
    assert parse_time_input("83.456", current=0.0, fps=0.0) == 83.46


def test_relative_frames_at_fps_zero_returns_current():
    # +6f with no fps = 0 frames of advancement
    assert parse_time_input("+6f", current=10.0, fps=0.0) == 10.0


# --- parse_relative_delta ---

def test_parse_relative_delta_positive_ms():
    assert parse_relative_delta("+250ms", fps=30.0) == 0.25


def test_parse_relative_delta_negative_seconds():
    assert parse_relative_delta("-1.5s", fps=30.0) == -1.5


def test_parse_relative_delta_negative_frames():
    # -6 frames at 30fps = -0.2s
    assert parse_relative_delta("-6f", fps=30.0) == -0.2


def test_parse_relative_delta_frames_without_fps_returns_none():
    assert parse_relative_delta("+6f", fps=0.0) is None


def test_parse_relative_delta_absolute_input_returns_none():
    assert parse_relative_delta("83.45", fps=30.0) is None


def test_parse_relative_delta_garbage_returns_none():
    assert parse_relative_delta("", fps=30.0) is None
    assert parse_relative_delta("+5x", fps=30.0) is None
    assert parse_relative_delta("hello", fps=30.0) is None
