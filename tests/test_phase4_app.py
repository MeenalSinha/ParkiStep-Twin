"""Streamlit application tests (need `streamlit`; skipped with a reason when it is not installed)."""
import os
from pathlib import Path

import pytest

st_testing = pytest.importorskip("streamlit.testing.v1", reason="streamlit is not installed")
ROOT = Path(__file__).resolve().parents[1]


def app(tmp_path=None, *env):
    return st_testing.AppTest.from_file(str(ROOT / "app" / "Home.py"), default_timeout=120)


def test_dashboard_starts_with_valid_configuration():
    at = app().run()
    assert not at.exception
    assert any("Research prototype" in (c.value if hasattr(c, "value") else "") for c in list(at.markdown) + list(at.caption))


def test_invalid_configuration_shows_actionable_error(tmp_path, monkeypatch):
    bad = tmp_path / "phase4.yaml"
    bad.write_text("phase4: {app: {}}\n")
    monkeypatch.setenv("PARKISTEP_PHASE4_CONFIG", str(bad))
    at = app().run()
    assert at.error and "configuration" in at.error[0].value.lower()


def test_no_replay_state_is_not_low_risk():
    at = app().run()
    text = " ".join(i.value for i in at.info)
    assert "not low risk" in text or at.error     # either the explicit empty state or an explicit dataset error
