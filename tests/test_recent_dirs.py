from datetime import datetime, timezone
from sign_manager.ui import recent_dirs


def test_migrate_legacy_string_list():
    old = ["/a/path", "/b/path"]
    new = recent_dirs.migrate(old)
    assert len(new) == 2
    assert new[0] == {"path": "/a/path", "last_opened": None, "file_count": None}
    assert new[1] == {"path": "/b/path", "last_opened": None, "file_count": None}


def test_migrate_already_new_form_is_idempotent():
    new = [{"path": "/a", "last_opened": "2026-01-01T00:00:00+00:00", "file_count": 5}]
    out = recent_dirs.migrate(new)
    assert out == new


def test_migrate_mixed_list_normalizes_strings():
    mixed = ["/legacy", {"path": "/new", "last_opened": None, "file_count": 3}]
    out = recent_dirs.migrate(mixed)
    assert out[0] == {"path": "/legacy", "last_opened": None, "file_count": None}
    assert out[1] == {"path": "/new", "last_opened": None, "file_count": 3}


def test_touch_updates_last_opened_and_count_and_promotes():
    items = [
        {"path": "/a", "last_opened": "2026-01-01T00:00:00+00:00", "file_count": 1},
        {"path": "/b", "last_opened": "2026-01-02T00:00:00+00:00", "file_count": 2},
    ]
    out = recent_dirs.touch(items, "/b", file_count=5,
                            now=datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc))
    assert out[0]["path"] == "/b"
    assert out[0]["file_count"] == 5
    assert out[0]["last_opened"] == "2026-05-17T12:00:00+00:00"
    assert out[1]["path"] == "/a"


def test_touch_new_path_inserts_at_front():
    items = [{"path": "/a", "last_opened": None, "file_count": None}]
    out = recent_dirs.touch(items, "/new", file_count=3,
                            now=datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc))
    assert out[0]["path"] == "/new"
    assert out[0]["file_count"] == 3
    assert out[1]["path"] == "/a"
