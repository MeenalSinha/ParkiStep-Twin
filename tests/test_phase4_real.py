"""Dashboard service against the REAL FoG-STAR tables and REAL trained Phase 2 artifacts (no synthetic data, no test doubles).
Skips with a reason when they are absent unless PARKISTEP_REQUIRE_REAL=1. Replays are in-sample software checks."""
import json

import pytest

from p3helpers import REPO, require_real
from parkistep.dashboard.replay_session import SessionStatus
from parkistep.dashboard.service import DashboardService, SelectionError, build_service
from parkistep.twin.config import load_phase3
from parkistep.twin.replay import ReplayRunner
from parkistep.twin.service import build_engine
from parkistep.twin.streams import build_replay_stream, load_processed_tables, select_recordings

N_CAP = 2500          # bounded replay keeps the test short; the full-recording equivalence was run manually (docs/phase4)
_CACHE: dict = {}


def real_service(tmp_path) -> DashboardService:
    if "base" not in _CACHE:
        base = build_service(REPO / "configs/phase4.yaml", REPO, ["replay.persist_state=false"], [f"replay.max_observations={N_CAP}"])
        require_real(base.model_description().get("available", False), "trained Phase 2 artifacts not available")
        ds = base.dataset_status()
        require_real(ds["available"], str(ds["problem"]))
        _CACHE["base"] = base.warm_up()
    return _CACHE["base"].fork()


def test_defaults_come_from_phase3_selection_and_are_valid(tmp_path):
    svc = real_service(tmp_path)
    d = svc.default_selection()
    s3 = load_phase3(REPO / "configs/phase3.yaml", REPO)
    sensor, _ = load_processed_tables(s3)
    assert [d["recording_id"]] == [str(x) for x in select_recordings(sensor, s3)]
    assert d["recording_id"] in [r["recording_id"] for r in svc.list_recordings(d["patient_id"])]


def test_selection_is_validated_against_real_data(tmp_path):
    svc = real_service(tmp_path)
    d = svc.default_selection()
    other = next(p["patient_id"] for p in svc.list_patients() if p["patient_id"] != d["patient_id"])
    with pytest.raises(SelectionError, match="belongs to participant"):
        svc.create_session(other, d["recording_id"])
    with pytest.raises(SelectionError):
        svc.create_session(d["patient_id"], "no-such-recording")
    with pytest.raises(SelectionError, match="not among the artifacts"):
        svc.create_session(d["patient_id"], d["recording_id"], "sensor_only/random_forest/h4")
    assert svc.sessions == {}                                           # failed selections created no twin


def test_service_replay_matches_blocking_phase3_replay(tmp_path):
    svc = real_service(tmp_path)
    d = svc.default_selection()
    sess = svc.create_session(d["patient_id"], d["recording_id"])
    assert svc.create_session(d["patient_id"], d["recording_id"]) is sess      # a rerun / double click creates no second twin
    sess.start()
    while not sess.is_finished:
        svc.advance(sess, "max")
    s = load_phase3(REPO / "configs/phase3.yaml", REPO, [f"replay.max_observations={N_CAP}"])
    sensor, _ = load_processed_tables(s)
    eng = build_engine(s, persistence=False)
    recs = select_recordings(sensor, s)
    stream, truth = build_replay_stream(sensor, s, recs, eng.handle.requirements.raw_channels)
    eng.create_twin(d["patient_id"])
    ref = ReplayRunner(s, eng).run(d["patient_id"], stream, truth)
    assert sess.status == SessionStatus.COMPLETED and sess.summary["determinism_digest"] == ref["determinism_digest"]
    assert sess.summary["counts"]["predictions"] == ref["counts"]["predictions"] > 0
    assert sess.summary["future_data_check"]["violations"] == 0
    assert svc.view(sess)["model_score"]["model"]["horizon_s"] == svc.model_description()["horizon_s"]


def test_ground_truth_only_after_replay_on_real_data(tmp_path):
    svc = real_service(tmp_path)
    d = svc.default_selection()
    sess = svc.create_session(d["patient_id"], d["recording_id"], replace=True)
    sess.start(); svc.advance(sess, 5)
    assert svc.retrospective(sess) is None
    sess.stop()
    assert svc.retrospective(sess)["partial_replay"] is True


def test_clinical_profile_keeps_missing_missing_and_follows_model_schema(tmp_path):
    svc = real_service(tmp_path)
    pid = svc.default_selection()["patient_id"]
    sensor_only = svc.clinical_profile(pid)
    assert sensor_only["model_uses_clinical"] is False and not any(f["used_by_selected_model"] for f in sensor_only["fields"])
    fus = next(m["key"] for m in svc.list_models() if m["uses_clinical"] and m["model"] == "random_forest" and m["horizon_s"] == 3.0)
    used = {f["field"] for f in svc.clinical_profile(pid, fus)["fields"] if f["used_by_selected_model"]}
    assert used == set(svc.model_description(fus)["clinical_profile_fields_used"]) and used
    assert all(f["value"] is None or f["status"] != "not_recorded" for f in sensor_only["fields"])


def test_performance_page_numbers_equal_saved_file(tmp_path):
    svc = real_service(tmp_path)
    perf = svc.performance()
    raw = json.loads(perf.metrics_path.read_text())["results"]
    for r in perf.rows():
        ev = raw[r["key"]]["event"]
        assert (r["n_events_detected"], r["n_events_evaluable"], r["false_alerts_per_hour"]) == (ev["n_events_detected"], ev["n_events_evaluable"], ev["false_alerts_per_hour"])
    assert len(perf.rows()) == len(raw)
