"""Tests for ring-based lazy folder preload in FolderPreloadWorker."""

from __future__ import annotations

import pytest


@pytest.fixture
def FolderPreloadWorker(qapp):
    """Import FolderPreloadWorker lazily: importing main_window at module
    collection time instantiates QShortcut/QKeySequence, which needs a
    live QApplication. The session-scoped ``qapp`` fixture guarantees one."""
    from sub_label_pos.ui.main_window import FolderPreloadWorker as _FPW
    return _FPW


class _NullSvc:
    """Stand-in VideoService that returns nothing — used so we can construct
    the worker without instantiating any real ffmpeg machinery. The worker's
    public ring math (`_compute_ring_indices`) is pure and doesn't call into
    the service at all, so these tests never start the QThreadPool."""

    def get_frame(self, p, s, **kw):  # noqa: D401, ARG002
        return None

    def get_dimensions(self, p):  # noqa: ARG002
        return (1920, 1080)

    def get_duration(self, p):  # noqa: ARG002
        return 0.0

    def get_fps(self, p):  # noqa: ARG002
        return 24.0


def _make_worker(FPW, n: int, ring: int):
    paths = [f"/x/file{i}.mkv" for i in range(n)]
    return FPW(paths, _NullSvc(), workers=1, ring=ring)


def test_ring_zero_means_unlimited(FolderPreloadWorker):
    """ring=0 keeps the legacy 'queue every file' behaviour."""
    w = _make_worker(FolderPreloadWorker, 10, ring=0)
    assert w._compute_ring_indices(active=0) == set(range(10))
    assert w._compute_ring_indices(active=5) == set(range(10))


def test_ring_two_queues_five_files(FolderPreloadWorker):
    w = _make_worker(FolderPreloadWorker, 10, ring=2)
    assert w._compute_ring_indices(active=5) == {3, 4, 5, 6, 7}


def test_ring_clips_to_edges(FolderPreloadWorker):
    """At the edges of the folder the ring is asymmetric (clipped), not wrapped."""
    w = _make_worker(FolderPreloadWorker, 10, ring=2)
    assert w._compute_ring_indices(active=0) == {0, 1, 2}
    assert w._compute_ring_indices(active=9) == {7, 8, 9}


def test_ring_one_three_files(FolderPreloadWorker):
    w = _make_worker(FolderPreloadWorker, 10, ring=1)
    assert w._compute_ring_indices(active=4) == {3, 4, 5}


def test_ring_larger_than_folder_returns_all(FolderPreloadWorker):
    w = _make_worker(FolderPreloadWorker, 3, ring=100)
    assert w._compute_ring_indices(active=1) == {0, 1, 2}


def test_set_active_index_before_start_only_updates_active(FolderPreloadWorker):
    """Before .start(), set_active_index just shifts the centre — it never
    submits to the pool. This matches the MainWindow flow where the active
    index is set first, then start() queues the initial ring."""
    w = _make_worker(FolderPreloadWorker, 10, ring=2)
    w.set_active_index(5)
    assert w._active_index == 5
    assert w._queued == set()


def test_set_active_index_after_start_extends_queued(FolderPreloadWorker, monkeypatch):
    """After .start(), shifting the active index queues any NEW ring members
    without resubmitting ones already queued."""
    w = _make_worker(FolderPreloadWorker, 10, ring=1)

    submitted: list[int] = []

    def _fake_start(task):
        # Record the index of the file this task was created for.
        # _RichFilePreloadTask stores the path on _path.
        submitted.append(int(task._path.rsplit("file", 1)[1].split(".")[0]))

    monkeypatch.setattr(w._pool, "start", _fake_start)

    w.set_active_index(0)
    w.start()
    # Initial ring: {0, 1}
    assert set(submitted) == {0, 1}
    assert w._queued == {0, 1}

    w.set_active_index(5)
    # New ring: {4, 5, 6}; queued set is now union.
    assert w._queued == {0, 1, 4, 5, 6}
    # Pool was asked to start three new tasks (4, 5, 6); ones already
    # queued are not resubmitted.
    assert set(submitted) == {0, 1, 4, 5, 6}


def test_set_active_index_does_not_resubmit_overlapping_files(FolderPreloadWorker, monkeypatch):
    """A small shift that overlaps the old ring queues only the new members."""
    w = _make_worker(FolderPreloadWorker, 10, ring=2)

    submitted: list[int] = []
    monkeypatch.setattr(
        w._pool, "start",
        lambda task: submitted.append(int(task._path.rsplit("file", 1)[1].split(".")[0]))
    )

    w.set_active_index(2)
    w.start()
    # Initial ring around 2: {0, 1, 2, 3, 4}
    assert set(submitted) == {0, 1, 2, 3, 4}
    submitted.clear()

    w.set_active_index(3)
    # New ring around 3: {1, 2, 3, 4, 5} — only 5 is new.
    assert submitted == [5]


def test_set_active_path_resolves_index(FolderPreloadWorker, monkeypatch):
    w = _make_worker(FolderPreloadWorker, 10, ring=1)
    monkeypatch.setattr(w._pool, "start", lambda task: None)
    w.set_active_path("/x/file7.mkv")
    assert w._active_index == 7


def test_set_active_path_unknown_path_is_noop(FolderPreloadWorker):
    w = _make_worker(FolderPreloadWorker, 10, ring=1)
    w.set_active_path("/nope.mkv")
    assert w._active_index == 0  # unchanged


def test_negative_ring_clamped_to_zero(FolderPreloadWorker):
    w = _make_worker(FolderPreloadWorker, 5, ring=-3)
    # Negative ring is treated as 'unlimited' — same as 0.
    assert w._compute_ring_indices(active=2) == {0, 1, 2, 3, 4}


def test_empty_folder_emits_all_done(FolderPreloadWorker):
    w = FolderPreloadWorker([], _NullSvc(), workers=1, ring=2)
    fired: list[bool] = []
    w.all_done.connect(lambda: fired.append(True))
    w.start()
    assert fired == [True]
