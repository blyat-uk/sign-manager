"""Tests for UndoStack — bounded history with same-key coalescing."""

import time

import pytest

from sign_manager.model.undo_stack import UndoStack


# Lightweight stand-in for Mutation (UndoStack doesn't need real apply/invert)
class _Fake:
    def __init__(self, name: str, key: str | None = None):
        self.name = name
        self.coalesce_key = key


def test_push_then_undo_returns_inverse(qapp):
    s = UndoStack(max_size=10, coalesce_window_ms=500)
    forward, inverse = _Fake("a"), _Fake("inv-a")
    s.push(forward, inverse)
    assert s.can_undo
    assert not s.can_redo
    got = s.undo()
    assert got is inverse


def test_redo_after_undo_returns_forward(qapp):
    s = UndoStack(max_size=10, coalesce_window_ms=500)
    f, inv = _Fake("a"), _Fake("inv-a")
    s.push(f, inv)
    s.undo()
    assert s.can_redo
    assert s.redo() is f


def test_push_clears_redo_stack(qapp):
    s = UndoStack(max_size=10, coalesce_window_ms=500)
    s.push(_Fake("a"), _Fake("inv-a"))
    s.undo()
    assert s.can_redo
    s.push(_Fake("b"), _Fake("inv-b"))
    assert not s.can_redo


def test_bounded_eviction(qapp):
    s = UndoStack(max_size=3, coalesce_window_ms=500)
    for i in range(5):
        s.push(_Fake(f"m{i}"), _Fake(f"inv-m{i}"))
    # only last 3 retained
    seen = []
    while s.can_undo:
        seen.append(s.undo().name)
    assert seen == ["inv-m4", "inv-m3", "inv-m2"]


def test_coalesce_window_collapses_same_key(qapp, monkeypatch):
    fake_now = [1000.0]
    monkeypatch.setattr("time.monotonic", lambda: fake_now[0])
    s = UndoStack(max_size=10, coalesce_window_ms=500)
    s.push(_Fake("a1", key="move:lid"), _Fake("inv-a1", key="move:lid"))
    fake_now[0] += 0.2  # 200ms — within 500ms window
    s.push(_Fake("a2", key="move:lid"), _Fake("inv-a2", key="move:lid"))
    # only one undo entry remains, but with the LATEST forward and ORIGINAL inverse
    assert s.size == 1
    assert s.undo().name == "inv-a1"   # original inverse preserved


def test_coalesce_outside_window_does_not_collapse(qapp, monkeypatch):
    fake_now = [1000.0]
    monkeypatch.setattr("time.monotonic", lambda: fake_now[0])
    s = UndoStack(max_size=10, coalesce_window_ms=500)
    s.push(_Fake("a1", key="move:lid"), _Fake("inv-a1", key="move:lid"))
    fake_now[0] += 0.6  # 600ms — outside window
    s.push(_Fake("a2", key="move:lid"), _Fake("inv-a2", key="move:lid"))
    assert s.size == 2


def test_no_coalesce_for_none_key(qapp, monkeypatch):
    fake_now = [1000.0]
    monkeypatch.setattr("time.monotonic", lambda: fake_now[0])
    s = UndoStack(max_size=10, coalesce_window_ms=500)
    s.push(_Fake("a", key=None), _Fake("inv-a", key=None))
    fake_now[0] += 0.1
    s.push(_Fake("b", key=None), _Fake("inv-b", key=None))
    assert s.size == 2


def test_no_coalesce_for_different_keys(qapp, monkeypatch):
    fake_now = [1000.0]
    monkeypatch.setattr("time.monotonic", lambda: fake_now[0])
    s = UndoStack(max_size=10, coalesce_window_ms=500)
    s.push(_Fake("a", key="move:1"), _Fake("inv-a", key="move:1"))
    fake_now[0] += 0.1
    s.push(_Fake("b", key="move:2"), _Fake("inv-b", key="move:2"))
    assert s.size == 2


def test_clear_resets_both_stacks(qapp):
    s = UndoStack(max_size=10, coalesce_window_ms=500)
    s.push(_Fake("a"), _Fake("inv-a"))
    s.undo()
    s.clear()
    assert not s.can_undo
    assert not s.can_redo


def test_can_undo_changed_signal(qapp):
    s = UndoStack(max_size=10, coalesce_window_ms=500)
    received = []
    s.can_undo_changed.connect(lambda b: received.append(b))
    s.push(_Fake("a"), _Fake("inv-a"))
    s.undo()
    assert received == [True, False]


def test_can_redo_changed_signal(qapp):
    s = UndoStack(max_size=10, coalesce_window_ms=500)
    received = []
    s.can_redo_changed.connect(lambda b: received.append(b))
    s.push(_Fake("a"), _Fake("inv-a"))
    s.undo()         # can_redo: True
    s.redo()         # can_redo: False
    assert received == [True, False]


def test_push_after_undo_emits_can_redo_false(qapp):
    s = UndoStack(max_size=10, coalesce_window_ms=500)
    s.push(_Fake("a"), _Fake("inv-a"))
    s.undo()
    received = []
    s.can_redo_changed.connect(lambda b: received.append(b))
    s.push(_Fake("b"), _Fake("inv-b"))
    assert received == [False]
