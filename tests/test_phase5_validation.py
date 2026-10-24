"""Phase 5 validation tests.

Real FoG-STAR tables and real trained Phase 2 artifacts are used wherever a test says so (skipped with a reason unless
PARKISTEP_REQUIRE_REAL=1). Configuration-failure tests use throw-away config files in tmp_path (synthetic, labelled).
Replays are capped by `replay.max_observations` so the suite stays short; this is a software check, not a clinical evaluation.
"""
import hashlib
import math
import re
from pathlib import Path

import pytest

from p3helpers import REPO, require_real
from parkistep.config import ConfigError
from parkistep.dashboard.replay_session import SessionStatus
from parkistep.dashboard.service import SelectionError, build_service

N_CAP = 1500
_CACHE: dict = {}


def real_base():
    if "base" not in _CACHE:
        base = build_service(REPO / "configs/phase4.yaml", REPO, ["replay.persist_state=false"], [f"replay.max_observations={N_CAP}"])
        require_real(base.model_description().get("available", False), "trained Phase 2 artifacts not available")
        ds = base.dataset_status()
        require_real(ds["available"], str(ds["problem"]))
        _CACHE["base"] = base.warm_up()
    return _CACHE["base"]


def run_full(svc, sel, slices=None):
    """Run one capped replay to the end. `slices` = observation counts per tick; the session is paused and resumed between
    ticks (a paused session is never advanced), so the digest must not depend on how the replay was sliced."""
    sess = svc.create_session(sel["patient_id"], sel["recording_id"], replace=True)
    sess.start()
    i = 0
    while not sess.is_finished:
        if slices:
            sess.advance(max_observations=slices[i % len(slices)])
            i += 1
            if not sess.is_finished:
                sess.pause()
                sess.resume()
        else:
            svc.advance(sess, "max")
    return sess


# ---------------------------------------------------------------- configuration failures (synthetic config files)
def test_missing_config_file_fails_early(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        build_service(tmp_path / "nope.yaml", REPO)


def test_malformed_yaml_fails_early(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("phase4: [unclosed\n  x: : :")
    with pytest.raises(ConfigError, match="not valid YAML"):
        build_service(bad, REPO)


def test_wrong_type_is_reported_with_the_setting_name():
    with pytest.raises(ConfigError, match=r"replay\.default_speed must be a number"):
        build_service(REPO / "configs/phase4.yaml", REPO, ["replay.default_speed=abc"])


def test_mistyped_override_is_rejected_not_ignored():
    """Phase 5 repair (ISSUE P5-001): an unknown override key used to be silently ignored."""
    with pytest.raises(ConfigError, match="Unknown Phase 4 setting"):
        build_service(REPO / "configs/phase4.yaml", REPO, ["replay.default_spedd=10"])


# ---------------------------------------------------------------- model failures: unavailable is explicit, never low risk
@pytest.mark.parametrize("p3_override", ["model.horizon_s=99", "model.model=no_such_model"])
def test_unavailable_model_is_reported_explicitly(p3_override):
    svc = build_service(REPO / "configs/phase4.yaml", REPO, [], [p3_override])
    d = svc.model_description()
    assert d["available"] is False and d["problem"]
    assert "probability" not in d and "threshold" not in d       # no invented score or threshold


def test_missing_artifact_directory_is_reported_and_creates_no_twin(tmp_path):
    svc = build_service(REPO / "configs/phase4.yaml", REPO, [], [f"model.artifact_dir={tmp_path / 'none'}"])
    d = svc.model_description()
    assert d["available"] is False and d.get("next_step")
    assert svc.sessions == {}


# ---------------------------------------------------------------- prediction integrity on real data
def test_forecast_integrity_on_real_replay():
    svc = real_base().fork()
    sel = svc.default_selection()
    sess = run_full(svc, sel)
    assert sess.status == SessionStatus.COMPLETED
    frame = svc.forecast_frame(sess, n=10 ** 9)
    assert len(frame) > 0, "the real replay produced no forecasts - the integrity checks below would be vacuous"
    assert (frame["window_end_time"] <= frame["prediction_time"] + 1e-9).all()
    assert (frame["window_start_time"] < frame["window_end_time"]).all()
    assert frame["probability"].map(math.isfinite).all() and frame["probability"].between(0, 1).all()
    assert frame["prediction_time"].is_monotonic_increasing
    assert frame["prediction_id"].is_unique
    assert sess.summary["future_data_check"]["violations"] == 0
    desc = svc.model_description()
    assert set(frame["model_id"]) == {desc["identity"]["model_id"]} if "model_id" in desc.get("identity", {}) else frame["model_id"].nunique() == 1
    assert frame["threshold"].nunique() == 1                      # one operating threshold for the whole replay
    assert float(frame["threshold"].iloc[0]) == pytest.approx(float(desc["threshold"]["value"] if isinstance(desc["threshold"], dict) else desc["threshold"]))
    assert (frame["above_threshold"] == (frame["probability"] >= frame["threshold"])).all()
    assert (frame["horizon_s"] == desc["horizon_s"]).all()


def test_warnings_are_tied_to_real_predictions_and_not_early_by_label():
    svc = real_base().fork()
    sess = run_full(svc, svc.default_selection())
    ids = set(svc.forecast_frame(sess, n=10 ** 9)["prediction_id"])
    warns = svc.warnings(sess)
    assert warns, "no warnings were produced in the capped replay"
    for w in warns:
        assert w["activation_prediction_id"] in ids
        assert w["activated_at"] <= (w.get("resolved_at") or float("inf"))
        assert w["lifecycle"][0]["to"] == "ACTIVE"


# ---------------------------------------------------------------- reproducibility (real data, real model)
def test_replay_is_reproducible_across_runs_and_across_pause_resume_slicing():
    svc = real_base().fork()
    sel = svc.default_selection()
    d1 = run_full(svc, sel).summary["determinism_digest"]
    d2 = run_full(svc, sel).summary["determinism_digest"]
    d3 = run_full(svc, sel, slices=[37, 211, 5, 90]).summary["determinism_digest"]
    assert d1 == d2 == d3


def test_paused_session_does_not_advance():
    svc = real_base().fork()
    sel = svc.default_selection()
    sess = svc.create_session(sel["patient_id"], sel["recording_id"], replace=True)
    sess.start()
    svc.advance(sess, "max")
    sess.pause()
    assert sess.status == SessionStatus.PAUSED
    before = (sess.progress().offered, len(sess.observations_frame()))
    rep = sess.advance(max_observations=100)
    after = (sess.progress().offered, len(sess.observations_frame()))
    assert rep.offered == 0 and "nothing was offered" in rep.note      # documented no-op, not an error and not a silent ingest
    assert before == after
    sess.resume()
    assert sess.status == SessionStatus.RUNNING


def test_double_start_and_rerun_create_no_second_twin():
    svc = real_base().fork()
    sel = svc.default_selection()
    a = svc.create_session(sel["patient_id"], sel["recording_id"], replace=True)
    b = svc.create_session(sel["patient_id"], sel["recording_id"])
    assert a is b and len(svc.sessions) == 1


def test_selecting_another_recording_of_wrong_patient_is_refused():
    svc = real_base().fork()
    sel = svc.default_selection()
    other = next(p["patient_id"] for p in svc.list_patients() if p["patient_id"] != sel["patient_id"])
    with pytest.raises(SelectionError):
        svc.create_session(other, sel["recording_id"])


# ---------------------------------------------------------------- source data is never modified by validation
def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def test_replay_does_not_modify_source_data_or_model_artifacts():
    real_base()
    files = [p for d in ("data", "artifacts/phase2") for p in sorted((REPO / d).rglob("*")) if p.is_file()]
    assert files, "no data/artifact files found"
    before = {str(p): _sha(p) for p in files}
    svc = real_base().fork()
    run_full(svc, svc.default_selection())
    after = {str(p): _sha(p) for p in files}
    assert before == after


# ---------------------------------------------------------------- hardcoding guard (identifiers come from the data at runtime)
def test_dashboard_sources_embed_no_participant_recording_or_absolute_paths():
    svc = real_base().fork()
    sel = svc.default_selection()
    needles = [sel["recording_id"], str(svc.model_description().get("model_id", ""))]
    files = list((REPO / "app").rglob("*.py")) + list((REPO / "src/parkistep/dashboard").rglob("*.py"))
    assert files
    for f in files:
        text = f.read_text()
        for n in needles:
            if n:
                assert n not in text, f"{f.name} embeds the identifier {n!r}"
        assert not re.search(r"""["'](/home/|/mnt/|/Users/|[A-Z]:\\\\)""", text), f"{f.name} embeds an absolute path"
