"""The app's names and version live in one place each."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from sign_manager import identity
from sign_manager.version import __version__

ROOT = Path(__file__).resolve().parents[1]


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_version_is_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)


def test_pyproject_version_matches_version_py():
    data = _pyproject()
    assert data["project"]["version"] == __version__
    assert data["tool"]["briefcase"]["version"] == __version__


def test_briefcase_names_match_identity():
    data = _pyproject()
    briefcase = data["tool"]["briefcase"]
    assert briefcase["project_name"] == identity.APP_NAME
    assert f'{briefcase["bundle"]}.{identity.APP_SLUG}' == identity.BUNDLE_ID
    assert identity.APP_SLUG in briefcase["app"]
    assert data["project"]["name"] == identity.APP_SLUG
    assert briefcase["url"] == identity.REPO_URL


def test_no_hard_coded_legacy_names_in_source():
    src = ROOT / "src" / "sign_manager"
    offenders = [
        str(p.relative_to(ROOT))
        for p in src.rglob("*.py")
        if p.name != "identity.py"
        and re.search(r"Sub Label Pos|SubLabelPos", p.read_text(encoding="utf-8"))
    ]
    assert offenders == []
