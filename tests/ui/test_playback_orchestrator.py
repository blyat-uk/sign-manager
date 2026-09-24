"""Tests for PlaybackOrchestrator — mode + time coordination.

These tests use Mock widgets rather than the real mpv/video widgets so
they stay fast and don't require a working OpenGL context or ffmpeg.
"""

from unittest.mock import Mock

from sign_manager.ui.controllers.playback_orchestrator import PlaybackOrchestrator


def _make_mpv_mock(*, is_file_loaded: bool = True, time_pos: float = 0.0) -> Mock:
    """Build a Mock that quacks like MpvPreviewWidget for the calls the
    orchestrator makes (play/pause/seek/capture/time_pos/is_file_loaded)."""
    m = Mock()
    m.is_file_loaded = is_file_loaded
    m.time_pos = time_pos
    # capture_frame returns None to skip the QImage path entirely.
    m.capture_frame.return_value = None
    return m


def _make_editor_mock(*, current_time: float = 0.0) -> Mock:
    e = Mock()
    e._current_time = current_time
    return e


def test_initial_state(qapp):
    p = PlaybackOrchestrator()
    assert p.mode == "edit"
    assert p.is_edit
    assert not p.is_playback


def test_enter_playback_requires_video_loaded(qapp):
    p = PlaybackOrchestrator()
    p.attach_mpv_widget(_make_mpv_mock())
    received: list[str] = []
    p.mode_changed.connect(received.append)

    # No video loaded — should be a no-op.
    p.enter_playback_mode()
    assert p.mode == "edit"
    assert received == []


def test_enter_playback_mode_emits_and_plays(qapp):
    p = PlaybackOrchestrator()
    mpv = _make_mpv_mock(is_file_loaded=True)
    p.attach_mpv_widget(mpv)
    p.set_video_loaded(True)

    received: list[str] = []
    p.mode_changed.connect(received.append)

    p.enter_playback_mode()
    assert received == ["playback"]
    assert p.mode == "playback"
    mpv.seek_absolute.assert_called_once_with(0.0)
    mpv.play.assert_called_once()


def test_enter_same_mode_no_emit(qapp):
    p = PlaybackOrchestrator()
    received: list[str] = []
    p.mode_changed.connect(received.append)
    p.enter_edit_mode()  # already edit
    assert received == []


def test_enter_edit_mode_pauses_mpv_and_emits(qapp):
    p = PlaybackOrchestrator()
    mpv = _make_mpv_mock(is_file_loaded=True, time_pos=4.5)
    editor = _make_editor_mock()
    p.attach_mpv_widget(mpv)
    p.attach_editor_widget(editor)
    p.set_video_loaded(True)
    p.enter_playback_mode()

    received: list[str] = []
    p.mode_changed.connect(received.append)
    p.enter_edit_mode()

    assert received == ["edit"]
    assert p.mode == "edit"
    mpv.pause.assert_called_once()
    # Editor's current_time should be synced from mpv's time_pos.
    assert editor._current_time == 4.5


def test_start_time_provider_is_used(qapp):
    p = PlaybackOrchestrator()
    mpv = _make_mpv_mock(is_file_loaded=True)
    p.attach_mpv_widget(mpv)
    p.set_video_loaded(True)
    p.set_start_time_provider(lambda: 12.5)

    p.enter_playback_mode()
    mpv.seek_absolute.assert_called_once_with(12.5)


def test_on_mpv_time_pos_forwards_to_timeline_in_playback(qapp):
    p = PlaybackOrchestrator()
    mpv = _make_mpv_mock(is_file_loaded=True)
    timeline = Mock()
    p.attach_mpv_widget(mpv)
    p.attach_timeline_widget(timeline)
    p.set_video_loaded(True)
    p.enter_playback_mode()
    timeline.reset_mock()  # forget the set_playing(True) from entering

    p.on_mpv_time_pos(3.25)
    timeline.set_time.assert_called_once_with(3.25)


def test_on_mpv_time_pos_does_nothing_in_edit_mode(qapp):
    p = PlaybackOrchestrator()
    timeline = Mock()
    p.attach_timeline_widget(timeline)
    # mode defaults to edit
    p.on_mpv_time_pos(3.25)
    timeline.set_time.assert_not_called()


def test_toggle_alternates_modes(qapp):
    p = PlaybackOrchestrator()
    mpv = _make_mpv_mock(is_file_loaded=True)
    editor = _make_editor_mock()
    p.attach_mpv_widget(mpv)
    p.attach_editor_widget(editor)
    p.set_video_loaded(True)

    received: list[str] = []
    p.mode_changed.connect(received.append)

    p.toggle()  # edit -> playback
    p.toggle()  # playback -> edit
    p.toggle()  # edit -> playback

    assert received == ["playback", "edit", "playback"]


def test_on_mpv_eof_returns_to_edit_mode(qapp):
    p = PlaybackOrchestrator()
    mpv = _make_mpv_mock(is_file_loaded=True)
    editor = _make_editor_mock()
    p.attach_mpv_widget(mpv)
    p.attach_editor_widget(editor)
    p.set_video_loaded(True)
    p.enter_playback_mode()

    received: list[str] = []
    p.mode_changed.connect(received.append)
    p.on_mpv_eof()

    assert received == ["edit"]
    assert p.mode == "edit"
    # Capture should be skipped on EOF (last frame is often black).
    mpv.capture_frame.assert_not_called()


def test_reset_to_edit_forces_edit_mode_without_pausing_mpv(qapp):
    p = PlaybackOrchestrator()
    mpv = _make_mpv_mock(is_file_loaded=True)
    p.attach_mpv_widget(mpv)
    p.set_video_loaded(True)
    p.enter_playback_mode()
    mpv.reset_mock()

    received: list[str] = []
    p.mode_changed.connect(received.append)
    p.reset_to_edit()

    assert received == ["edit"]
    assert p.mode == "edit"
    # reset_to_edit is for "file just reloaded, mpv state is fresh" — it
    # should not call pause/play.
    mpv.pause.assert_not_called()


def test_video_unload_falls_back_to_edit(qapp):
    p = PlaybackOrchestrator()
    mpv = _make_mpv_mock(is_file_loaded=True)
    p.attach_mpv_widget(mpv)
    p.set_video_loaded(True)
    p.enter_playback_mode()
    assert p.mode == "playback"

    received: list[str] = []
    p.mode_changed.connect(received.append)
    p.set_video_loaded(False)
    assert received == ["edit"]
    assert p.mode == "edit"
