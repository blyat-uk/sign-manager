"""Tests for the startup helpers in app.py."""

from __future__ import annotations

import io
import json
import sys

import pytest

from sign_manager import app


def test_parse_args_passes_unknown_args_to_qt():
    args, rest = app.parse_args(["--self-test", "--report", "r.json", "-platform", "offscreen"])
    assert args.self_test and args.report == "r.json"
    assert rest == ["-platform", "offscreen"]


def test_parse_args_quit_after():
    args, _ = app.parse_args(["--quit-after", "2.5"])
    assert args.quit_after == 2.5


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        app.main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"Sign Manager {app.__version__}"


def test_redirect_leaves_working_streams_alone(tmp_path):
    assert app.redirect_std_streams(tmp_path) is None
    assert not list(tmp_path.iterdir())


def test_redirect_when_streams_are_none(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    (tmp_path / "sign-manager.log").write_text("previous run", encoding="utf-8")

    path = app.redirect_std_streams(tmp_path)
    try:
        assert path == tmp_path / "sign-manager.log"
        print("hello")
        sys.stderr.write("oops\n")
        sys.stdout.flush()
        assert path.read_text(encoding="utf-8") == "hello\noops\n"
        assert (tmp_path / "sign-manager.log.1").read_text(encoding="utf-8") == "previous run"
    finally:
        sys.stdout.close()


def test_redirect_when_stream_closed(tmp_path, monkeypatch):
    closed = io.StringIO()
    closed.close()
    monkeypatch.setattr(sys, "stderr", closed)
    path = app.redirect_std_streams(tmp_path)
    try:
        assert path is not None and sys.stderr is not closed
        assert sys.stdout is sys.__stdout__ or sys.stdout is not closed
    finally:
        sys.stderr.close()


def test_install_runtime_deps_unsupported_host(monkeypatch, capsys):
    from sign_manager.services import runtime_download

    monkeypatch.setattr(runtime_download, "artifact_for_host", lambda: None)
    assert app.install_runtime_deps_cli() == 1
    assert "no download" in capsys.readouterr().out


def test_self_test_writes_report(tmp_path, monkeypatch):
    from sign_manager import self_test
    from sign_manager.services import runtime_deps

    monkeypatch.setattr(
        runtime_deps, "resolve",
        lambda: runtime_deps.RuntimeTools(None, None, None),
    )
    report = tmp_path / "report.json"
    assert self_test.run(report=str(report)) == 1
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["ok"] is False
    assert data["version"] == app.__version__
    assert data["tools"]["missing"] == ["libmpv", "ffmpeg", "ffprobe"]
    assert data["ffmpeg"]["ok"] is False
