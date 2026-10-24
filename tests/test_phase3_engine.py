"""Twin state, lifecycle, forecast integration and runtime warning handling (SYNTHETIC data + scripted predictor test double).
Real trained-model and real-data checks are in test_phase3_real_model.py / test_phase3_real_data.py."""
import dataclasses
import math

import numpy as np
import pytest

from p3helpers import DT, N_WIN, StubPredictor, make_engine, make_manifest, rec, settings, stream
from parkistep.twin.engine import TwinEngine, observation_clock
from parkistep.twin.errors import (ModelUnavailableError, PatientMismatchError, TwinError, TwinExistsError, UnknownTwinError)
from parkistep.twin.model import handle_from_predictor, unavailable
from parkistep.twin.service import TwinService
from parkistep.twin.state import StatusFacts, TwinStatus, derive_status

CLIN = {"participant_id": 3, "age": 70.0, "gender": "F", "disease_duration": 5.0, "hoehn_yahr": 2.0, "updrs_iii": 30.0, "fog_q": 12.0,
        "moca": None, "fes_i": 20.0, "pdq_8": 10.0}          # canonical Phase 1 clinical schema (SYNTHETIC values)


def feed(eng, s, n, pid=1, **kw):
    out = []
    for r in stream(s, n, patient=pid, **kw):
        out.append(eng.ingest(pid, r))
    return out


# ------------------------------------------------------------------ twin creation and state schema
def test_twin_initialises_with_identity_lifecycle_and_honest_empty_state(tmp_path):
    eng, stub, s = make_engine(tmp_path)
    st = eng.create_twin(5)
    d = st.to_dict()
    assert d["twin_id"] == "twin-5" and d["patient_id"] == "5" and d["status"] == "WAITING_FOR_DATA" and d["update_seq"] >= 1
    assert d["created_at"] and d["updated_at"] and d["schema_version"].startswith("parkistep.twin.state/")
    for section in ("clinical", "stream", "forecast", "warning", "baseline", "model", "config"):
        assert section in d
    assert d["clinical"]["status"] == "absent" and d["clinical"]["values"] == {} and "moca" in d["clinical"]["missing_fields"]
    assert d["forecast"]["status"] == "none" and d["forecast"]["latest_prediction_id"] is None
    assert d["model"]["clinical_influences_forecast"] is False
    assert "not a diagnosis" in d["disclaimer"].lower()


def test_missing_clinical_fields_are_reported_not_invented(tmp_path):
    eng, _, s = make_engine(tmp_path)
    st = eng.create_twin(3, clinical=CLIN)
    c = st.clinical
    assert c.status == "partial" and c.missing_fields == ["moca"] and c.values["moca"] is None and c.validation["moca"] == "missing"
    assert c.validation["age"] == "ok" and c.provenance["key_value"] == "3" and len(c.provenance["row_sha256"]) == 64


def test_out_of_range_clinical_value_is_flagged_but_kept(tmp_path):
    eng, _, s = make_engine(tmp_path)
    st = eng.create_twin(3, clinical=CLIN | {"updrs_iii": 200.0, "moca": 20.0})            # the profile defines updrs_iii in [0, 76]
    assert st.clinical.validation["updrs_iii"] == "out_of_range" and st.clinical.values["updrs_iii"] == 200.0 and st.clinical.status == "partial"
    assert st.clinical.validation["age"] == "ok"                                              # no range defined for age -> not flagged


def test_clinical_record_of_another_patient_is_refused(tmp_path):
    eng, _, s = make_engine(tmp_path)
    with pytest.raises(PatientMismatchError, match="belongs to patient"):
        eng.create_twin(4, clinical=CLIN)                              # record says patient 3
    with pytest.raises(PatientMismatchError, match="cannot be verified"):
        eng.create_twin(4, clinical={k: v for k, v in CLIN.items() if k != "participant_id"})
    assert not eng.has_twin(4)


def test_patient_identifier_validation_and_exists_policy(tmp_path):
    eng, _, s = make_engine(tmp_path)
    with pytest.raises(TwinError, match="invalid patient identifier"):
        eng.create_twin("bad id/../x")
    eng.create_twin(1)
    with pytest.raises(TwinExistsError):
        eng.create_twin(1)
    eng2, _, _ = make_engine(tmp_path / "b", "patient.exists_policy=return_existing")
    a = eng2.create_twin(1)
    assert eng2.create_twin(1) is a


def test_unknown_twin_is_an_explicit_error(tmp_path):
    eng, _, s = make_engine(tmp_path)
    with pytest.raises(UnknownTwinError, match="no twin for patient"):
        eng.get_twin(99)
    with pytest.raises(UnknownTwinError):
        eng.ingest(99, rec(s, 0))


def test_require_clinical_record_policy(tmp_path):
    eng, _, s = make_engine(tmp_path, "patient.require_clinical_record=true")
    with pytest.raises(TwinError, match="no clinical record"):
        eng.create_twin(1)
    assert eng.create_twin(3, clinical=CLIN).patient_id == "3"


def test_twin_id_template_is_configurable(tmp_path):
    eng, _, s = make_engine(tmp_path, "patient.twin_id_template=dt::{patient_id}")
    assert eng.create_twin(8).twin_id == "dt::8"


# ------------------------------------------------------------------ lifecycle
@pytest.mark.parametrize("facts,expected", [
    (dict(error=True, model_available=True, stale=False, has_observation=True, window_complete=True, window_quality_ok=True), "ERROR"),
    (dict(error=False, model_available=False, stale=False, has_observation=True, window_complete=True, window_quality_ok=True), "MODEL_UNAVAILABLE"),
    (dict(error=False, model_available=True, stale=True, has_observation=True, window_complete=True, window_quality_ok=True), "STALE"),
    (dict(error=False, model_available=True, stale=False, has_observation=False, window_complete=False, window_quality_ok=False), "WAITING_FOR_DATA"),
    (dict(error=False, model_available=True, stale=False, has_observation=True, window_complete=False, window_quality_ok=False), "WARMING_UP"),
    (dict(error=False, model_available=True, stale=False, has_observation=True, window_complete=True, window_quality_ok=False), "DEGRADED_DATA_QUALITY"),
    (dict(error=False, model_available=True, stale=False, has_observation=True, window_complete=True, window_quality_ok=True), "READY"),
    (dict(error=False, model_available=True, stale=False, has_observation=True, window_complete=True, window_quality_ok=True, clinical_ok=False), "DEGRADED_DATA_QUALITY"),
])
def test_status_derivation_table_is_deterministic(facts, expected):
    a, b = derive_status(StatusFacts(**facts)), derive_status(StatusFacts(**facts))
    assert a == b and a[0].value == expected and a[1]


def test_full_lifecycle_flow_and_audit_trail(tmp_path):
    eng, stub, s = make_engine(tmp_path)
    eng.create_twin(1)
    seen = [eng.get_twin(1).status]
    for r in stream(s, 25):
        eng.ingest(1, r)
        if eng.get_twin(1).status != seen[-1]:
            seen.append(eng.get_twin(1).status)
    for i in range(25, 45):                                     # the wrist sensor dies
        eng.ingest(1, rec(s, i, nan_channels=("wrist",)))
        if eng.get_twin(1).status != seen[-1]:
            seen.append(eng.get_twin(1).status)
    for i in range(45, 70):                                     # and recovers
        eng.ingest(1, rec(s, i))
        if eng.get_twin(1).status != seen[-1]:
            seen.append(eng.get_twin(1).status)
    assert seen == ["WAITING_FOR_DATA", "WARMING_UP", "READY", "DEGRADED_DATA_QUALITY", "READY"]
    changes = eng.event_history(1, "STATUS_CHANGE")
    assert [e.detail["to"] for e in changes] == seen and all(e.detail["reason"] for e in changes)      # creation transition is audited too


def test_stale_status_via_tick_and_recovery_on_new_data(tmp_path):
    eng, stub, s = make_engine(tmp_path)
    eng.create_twin(1)
    feed(eng, s, 30)
    assert eng.get_twin(1).status == "READY"
    st = eng.tick(1, 30 * DT + 1.0)
    assert st.status == "READY"                                  # within lifecycle.stale_after_s (5 s)
    st = eng.tick(1, 30 * DT + 10.0)
    assert st.status == "STALE" and st.forecast.status == "stale"
    eng.ingest(1, rec(s, 100, ts=30 * DT + 10.0))                # data resumes after a long gap: window reset, warm-up again
    assert eng.get_twin(1).status == "WARMING_UP"


def test_model_unavailable_degrade_mode_tracks_data_but_never_forecasts(tmp_path):
    s = settings(tmp_path, "model.on_unavailable=degrade")
    eng = TwinEngine(s, handle=unavailable(["artifact missing"]), clock=observation_clock)
    st = eng.create_twin(1)
    assert st.status == "MODEL_UNAVAILABLE" and st.model["status"] == "unavailable" and st.model["problems"] == ["artifact missing"]
    for r in stream(s, 40):
        res = eng.ingest(1, r)
        assert res.accepted and res.prediction is None
    st = eng.get_twin(1)
    assert st.status == "MODEL_UNAVAILABLE" and st.stream.n_accepted_total == 40 and st.forecast.n_predictions == 0
    assert any(e.kind == "MODEL_UNAVAILABLE" for e in eng.event_history(1))


def test_unexpected_predictor_failure_sets_error_and_engine_survives(tmp_path):
    eng, stub, s = make_engine(tmp_path)
    boom = {"n": 0}
    orig = stub.predict

    def flaky(window, ts, clinical=None, metadata=None):
        boom["n"] += 1
        if boom["n"] == 2:
            raise RuntimeError("synthetic failure")
        return orig(window, ts, clinical, metadata)
    stub.predict = flaky
    eng.create_twin(1)
    statuses = []
    for r in stream(s, 40):
        eng.ingest(1, r)
        statuses.append(eng.get_twin(1).status)
    assert "ERROR" in statuses and statuses[-1] == "READY"
    err = [e for e in eng.event_history(1) if e.kind == "ERROR"]
    assert err and err[0].detail["type"] == "RuntimeError" and "traceback" not in err[0].detail
    assert eng.get_twin(1).stream.n_accepted_total == 40


# ------------------------------------------------------------------ prediction records and honesty of forecasts
def test_prediction_records_are_complete_unique_and_immutable(tmp_path):
    eng, stub, s = make_engine(tmp_path)
    eng.create_twin(1)
    feed(eng, s, 40)
    ps = eng.recent_predictions(1, 100)
    assert len({p.prediction_id for p in ps}) == len(ps) == len(stub.calls)
    p = ps[0]
    for f in ("prediction_id", "twin_id", "patient_id", "session_id", "prediction_time", "window_start_time", "window_end_time", "probability",
              "horizon_s", "model_id", "model_artifact_sha256", "threshold", "threshold_source", "data_quality", "processing_time_utc", "state_version"):
        assert getattr(p, f) is not None
    assert p.window_end_time - p.window_start_time == pytest.approx(1.0) and p.horizon_s == 3.0 and p.twin_id == "twin-1"
    assert "not a calibrated probability" in p.score_semantics
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.probability = 0.99
    snap = [x.to_dict() for x in ps]
    feed_more = [eng.ingest(1, rec(s, i)) for i in range(40, 60)]
    assert [x.to_dict() for x in eng.recent_predictions(1, 100)][:len(snap)] == snap          # old records never change


def test_every_window_ends_at_the_triggering_observation_and_future_data_cannot_leak(tmp_path):
    sc = lambda t: 0.3
    full, sf, s = make_engine(tmp_path / "f", score=sc)
    trunc, st_, _ = make_engine(tmp_path / "t", score=sc)
    recs = stream(s, 80)
    for e in (full, trunc):
        e.create_twin(1)
    for r in recs:
        full.ingest(1, r)
    for r in recs[:50]:
        trunc.ingest(1, r)
    for c, p in zip(sf.calls, full.recent_predictions(1, 999)):
        assert c["window"].timestamp_s.max() == pytest.approx(p.prediction_time)
    # the first 50 samples' forecasts are bit-identical whether or not later samples ever existed
    n = len(st_.calls)
    for a, b in zip(sf.calls[:n], st_.calls):
        assert a["window"].equals(b["window"])


def test_old_forecast_is_never_presented_as_new(tmp_path):
    eng, stub, s = make_engine(tmp_path, score=0.2)
    svc = TwinService(eng)
    eng.create_twin(1)
    feed(eng, s, 30)
    v = svc.view(1)
    assert v["model_score"]["is_current"] and v["model_score"]["current_forecast"]["prediction_id"] == v["model_score"]["latest_computed_forecast"]["prediction_id"]
    last_id = eng.latest_prediction(1).prediction_id
    n_calls = len(stub.calls)
    for i in range(30, 50):                                    # sensor failure: windows invalid, model not called
        eng.ingest(1, rec(s, i, nan_channels=("back",)))
    assert len(stub.calls) == n_calls
    v = svc.view(1)
    assert eng.latest_prediction(1).prediction_id == last_id and eng.get_twin(1).forecast.latest_prediction_time < 49 * DT
    assert v["model_score"]["forecast_status"] == "unavailable" and v["model_score"]["is_current"] is False
    assert v["model_score"]["current_forecast"] is None and v["model_score"]["latest_computed_forecast"]["prediction_id"] == last_id
    assert v["model_score"]["latest_forecast_age_s"] > 0


def test_prediction_timestamp_changes_only_when_inference_happens(tmp_path):
    eng, stub, s = make_engine(tmp_path)
    eng.create_twin(1)
    last = None
    for r in stream(s, 40):
        res = eng.ingest(1, r)
        st = eng.get_twin(1)
        if res.prediction is None:
            assert st.forecast.latest_prediction_time == last
        else:
            assert st.forecast.latest_prediction_time == res.prediction.prediction_time
        last = st.forecast.latest_prediction_time
    assert eng.get_twin(1).stream.latest_observation_ts > last - 1e-9


# ------------------------------------------------------------------ sensor-only vs fusion
def test_sensor_only_model_gets_no_clinical_input_and_is_not_credited(tmp_path):
    eng, stub, s = make_engine(tmp_path, uses_clinical=False)
    eng.create_twin(3, clinical=CLIN | {"moca": 22.0})
    feed(eng, s, 30, pid=3)
    assert all(c["clinical"] is None for c in stub.calls)
    p = eng.latest_prediction(3)
    assert p.uses_clinical is False and p.clinical_used is False
    v = TwinService(eng).view(3)
    assert v["clinical_context"]["influences_forecast"] is False and v["clinical_context"]["values"]["age"] == 70.0   # kept, not used


def test_fusion_model_receives_the_patients_clinical_fields(tmp_path):
    eng, stub, s = make_engine(tmp_path, uses_clinical=True, manifest_kw={"experiment": "fusion"}, **{}) if False else make_engine(
        tmp_path, "model.experiment=fusion", uses_clinical=True, manifest_kw={"experiment": "fusion"})
    eng.create_twin(3, clinical=CLIN | {"moca": 22.0})
    feed(eng, s, 30, pid=3)
    assert stub.calls and all(c["clinical"] == {"age": 70.0, "moca": 22.0, "gender": "F"} for c in stub.calls)
    p = eng.latest_prediction(3)
    assert p.uses_clinical and p.clinical_used
    assert eng.get_twin(3).clinical.influences_forecast is True


def test_missing_clinical_value_goes_to_the_models_saved_policy_by_default(tmp_path):
    eng, stub, s = make_engine(tmp_path, "model.experiment=fusion", uses_clinical=True, manifest_kw={"experiment": "fusion"})
    eng.create_twin(3, clinical=CLIN)                          # moca missing
    feed(eng, s, 30, pid=3)
    assert stub.calls and stub.calls[0]["clinical"]["moca"] is None               # passed through as missing, not invented
    assert eng.get_twin(3).status == "READY"


def test_require_all_policy_blocks_fusion_forecast_when_a_field_is_missing(tmp_path):
    eng, stub, s = make_engine(tmp_path, "model.experiment=fusion", "patient.clinical_missing_policy=require_all", uses_clinical=True,
                               manifest_kw={"experiment": "fusion"})
    eng.create_twin(3, clinical=CLIN)
    feed(eng, s, 30, pid=3)
    assert stub.calls == [] and eng.get_twin(3).status == "DEGRADED_DATA_QUALITY"
    assert eng.get_twin(3).forecast.status == "unavailable" and "clinical" in eng.get_twin(3).forecast.status_reason


def test_clinical_feature_mapping_is_configurable(tmp_path):
    def mutate(cfg):
        cfg["patient"]["clinical_feature_mapping"] = {"age": "updrs_iii"}        # deliberately remapped: model field 'age' <- profile 'updrs_iii'
    eng, stub, s = make_engine(tmp_path, "model.experiment=fusion", uses_clinical=True, manifest_kw={"experiment": "fusion"}, mutate=mutate)
    eng.create_twin(3, clinical=CLIN | {"moca": 22.0})
    feed(eng, s, 30, pid=3)
    assert stub.calls[0]["clinical"]["age"] == 30.0


# ------------------------------------------------------------------ model compatibility
def test_horizon_mismatch_and_wrong_model_are_rejected_not_substituted(tmp_path):
    with pytest.raises(ModelUnavailableError, match="horizon"):
        make_engine(tmp_path, "model.horizon_s=5.0")                         # manifest says 3.0
    s = settings(tmp_path)
    stub = StubPredictor(make_manifest(s, model="other"))
    with pytest.raises(ModelUnavailableError, match="configuration selects"):
        handle_from_predictor(stub, s)


def test_model_missing_from_disk_fails_clearly_or_degrades_by_config(tmp_path):
    from parkistep.twin.model import load_model
    s = settings(tmp_path, "model.artifact_dir=" + str(tmp_path / "nowhere"))
    with pytest.raises(ModelUnavailableError, match="manifest not found"):
        load_model(s)
    s2 = settings(tmp_path, "model.artifact_dir=" + str(tmp_path / "nowhere"), "model.on_unavailable=degrade")
    h = load_model(s2)
    assert not h.available and "manifest not found" in h.problems[0]


def test_manifest_without_model_file_counts_as_unavailable(tmp_path):
    import json
    from parkistep.twin.model import load_model
    d = tmp_path / "models"; d.mkdir()
    s = settings(tmp_path, f"model.artifact_dir={d}", "model.experiment=sensor_only", "model.model=random_forest", "model.horizon_s=3.0")
    (d / "sensor_only__random_forest__h3.manifest.json").write_text(json.dumps({"artifact": "sensor_only__random_forest__h3.joblib"}))
    with pytest.raises(ModelUnavailableError, match="missing"):
        load_model(s)


def test_incompatible_channel_configuration_is_rejected(tmp_path):
    def mutate(cfg):
        cfg["observation"]["channels"] = {"mode": "mapping", "mapping": {"x": "ankleL_acc_x"}}   # does not cover the model's channels
    with pytest.raises(ModelUnavailableError, match="does not provide model channels"):
        make_engine(tmp_path, mutate=mutate)


def test_stricter_valid_fraction_is_allowed_weaker_is_not(tmp_path):
    eng, _, s = make_engine(tmp_path, "buffer.min_valid_fraction=0.99")
    assert eng.handle.requirements.min_valid_fraction == 0.99
    with pytest.raises(ModelUnavailableError, match="weaker"):
        make_engine(tmp_path / "w", "buffer.min_valid_fraction=0.5")


def test_window_requirements_come_from_the_manifest_not_from_configuration(tmp_path):
    eng, _, s = make_engine(tmp_path, manifest_kw={"window_s": 2.0, "dt": 0.05, "stride": 0.1})
    r = eng.handle.requirements
    assert (r.input_window_s, r.dt_s, r.n_samples, r.stride_s) == (2.0, 0.05, 41, 0.1) and eng.cadence == 0.1
    assert "input_window_s" not in str(s.cfg)


# ------------------------------------------------------------------ operating threshold
def test_threshold_comes_from_the_manifest_and_cannot_be_overridden(tmp_path):
    eng, stub, s = make_engine(tmp_path, "warning.threshold.fallback_value=0.99", manifest_kw={"threshold": 0.37})
    eng.create_twin(1)
    feed(eng, s, 30)
    p = eng.latest_prediction(1)
    assert p.threshold == 0.37 and p.threshold_source == "model_manifest" and eng.handle.threshold.value == 0.37


def test_missing_threshold_disables_warnings_unless_a_fallback_is_configured(tmp_path):
    eng, stub, s = make_engine(tmp_path, score=0.95, manifest_kw={"thr_block": {"policy": "none"}})
    eng.create_twin(1)
    feed(eng, s, 40)
    p = eng.latest_prediction(1)
    assert p.threshold is None and p.above_threshold is None and p.threshold_source == "unavailable"
    assert eng.get_twin(1).warning.n_warnings == 0
    assert any(e.kind == "WARNING_THRESHOLD_UNAVAILABLE" for e in eng.event_history(1))
    eng2, _, s2 = make_engine(tmp_path / "fb", "warning.threshold.fallback_value=0.9", score=0.95, manifest_kw={"thr_block": {"policy": "none"}})
    eng2.create_twin(1)
    feed(eng2, s2, 40)
    p2 = eng2.latest_prediction(1)
    assert p2.threshold == 0.9 and p2.threshold_source == "configured_fallback" and eng2.get_twin(1).warning.n_warnings >= 1


# ------------------------------------------------------------------ warnings inside the engine
def test_warning_activation_links_to_triggering_prediction(tmp_path):
    eng, stub, s = make_engine(tmp_path, score=lambda t: 0.9 if t >= 2.0 else 0.1)
    eng.create_twin(1)
    feed(eng, s, 40)
    w = eng.warning_history(1)
    assert len(w) == 1 and eng.current_warning(1).warning_id == w[0].warning_id
    pred = next(p for p in eng.recent_predictions(1, 999) if p.prediction_id == w[0].activation_prediction_id)
    assert pred.prediction_time == w[0].activated_at >= 2.0 and w[0].threshold == 0.5 and w[0].horizon_s == 3.0
    assert w[0].model_id == pred.model_id and "does not establish" in w[0].note
    assert eng.get_twin(1).warning.state == "active" and eng.get_twin(1).warning.active_warning_id == w[0].warning_id


def test_high_scores_on_invalid_data_create_no_warning(tmp_path):
    eng, stub, s = make_engine(tmp_path, score=0.99)
    eng.create_twin(1)
    for i in range(60):
        eng.ingest(1, rec(s, i, nan_channels=("ankleR",)))
    assert stub.calls == [] and eng.warning_history(1) == [] and eng.get_twin(1).warning.state == "none"


def test_active_warning_expires_when_forecasts_stay_unavailable(tmp_path):
    eng, stub, s = make_engine(tmp_path, "warning.expire_after_invalid_s=1.0", score=0.9)
    eng.create_twin(1)
    feed(eng, s, 30)
    assert eng.current_warning(1) is not None
    for i in range(30, 70):
        eng.ingest(1, rec(s, i, nan_channels=("wrist",)))
    w = eng.warning_history(1)[0]
    assert w.status == "EXPIRED" and "no valid forecast" in w.resolution_reason and eng.current_warning(1) is None
    assert w.lifecycle[0]["to"] == "ACTIVE" and w.lifecycle[-1]["to"] == "EXPIRED"       # history kept


def test_warning_policy_can_be_taken_from_configuration_instead_of_the_manifest(tmp_path):
    sc = lambda t: 0.9 if int(round(t / 0.2)) % 2 == 0 else 0.1
    eng, _, s = make_engine(tmp_path, "warning.policy_source=config", "warning.persistence_anchors=2", score=sc)
    eng.create_twin(1)
    feed(eng, s, 80)
    assert eng.get_twin(1).warning.n_warnings == 0                 # never two consecutive highs
    eng2, _, s2 = make_engine(tmp_path / "m", score=sc)           # manifest policy (persistence 1): fires
    eng2.create_twin(1)
    feed(eng2, s2, 80)
    assert eng2.get_twin(1).warning.n_warnings >= 1 and eng2.policy.source == "model_manifest"


def test_session_end_expires_the_active_warning_and_cooldown_does_not_cross_recordings(tmp_path):
    eng, stub, s = make_engine(tmp_path, "warning.policy_source=config", "warning.cooldown_s=1000", score=0.9)
    eng.create_twin(1)
    feed(eng, s, 30, session="a")
    assert eng.get_twin(1).warning.n_warnings == 1
    for i in range(30, 60):
        eng.ingest(1, rec(s, i, session="b"))
    ws = eng.warning_history(1)
    assert [w.status for w in ws][0] == "EXPIRED" and ws[0].resolution_reason.startswith("session ended")
    assert len(ws) == 2 and ws[1].session_id == "b"                 # new recording: cooldown starts over


# ------------------------------------------------------------------ isolation, retention, accessors
def test_twins_are_isolated(tmp_path):
    eng, stub, s = make_engine(tmp_path, score=lambda t: 0.9 if t > 2 else 0.1)
    eng.create_twin(1, clinical=CLIN | {"participant_id": 1})
    eng.create_twin(2)
    feed(eng, s, 40, pid=1)
    a, b = eng.get_twin(1), eng.get_twin(2)
    assert a.status == "READY" and b.status == "WAITING_FOR_DATA" and b.stream.n_accepted_total == 0 and b.forecast.n_predictions == 0
    assert eng.recent_predictions(2, 10) == [] and eng.warning_history(2) == [] and eng.recent_observations(2, 10) == []
    assert b.clinical.status == "absent" and a.clinical.status in ("partial", "valid")
    assert all(p.patient_id == "1" for p in eng.recent_predictions(1, 99))


def test_history_retention_is_bounded_and_counts_pruned(tmp_path):
    eng, _, s = make_engine(tmp_path, "retention.prediction_history_max=5", "retention.event_history_max=20")
    eng.create_twin(1)
    feed(eng, s, 120)
    st = eng.get_twin(1)
    assert len(eng.recent_predictions(1, 999)) == 5 and st.forecast.n_pruned == st.forecast.n_predictions - 5 > 0
    assert len(eng.event_history(1)) <= 20


def test_service_view_separates_score_policy_measurements_and_quality(tmp_path):
    eng, _, s = make_engine(tmp_path, score=0.9)
    svc = TwinService(eng)
    eng.create_twin(1)
    feed(eng, s, 30)
    v = svc.view(1)
    assert v["schema_version"] == "parkistep.twin.view/1"
    for k in ("model_score", "warning_policy", "observed", "data_quality", "clinical_context", "movement_baseline", "limitations", "disclaimer"):
        assert k in v
    assert "ground_truth" not in str(v.keys()).lower() and "fog" not in v["observed"]
    import json
    json.dumps(v)                                                 # fully JSON-serialisable


def test_baseline_updates_when_ready_freezes_during_warning_and_skips_outliers(tmp_path):
    eng, stub, s = make_engine(tmp_path, "baseline.min_updates=15", "baseline.skip_if_abs_z_above=4.0")
    eng.create_twin(1)
    feed(eng, s, 160)
    b = eng.get_twin(1).baseline
    assert b.enabled and b.status == "reporting" and "ankleL_acc" in b.positions
    assert b.n_updates == len(stub.calls) > 15                    # stationary data: every eligible window is absorbed
    assert all(v["z"] is not None for v in b.latest.values())
    n = b.n_updates
    for i in range(160, 200):                                     # abnormal, very energetic movement -> outlier safeguard
        r = rec(s, i)
        for k in list(r):
            if k.endswith(("_acc_x", "_acc_y", "_acc_z")):
                r[k] *= 50.0 * (1 + (i % 2))
        eng.ingest(1, r)
    b = eng.get_twin(1).baseline
    assert b.n_updates == n and b.status == "frozen"              # the abnormal windows did not drag the baseline
    eng2, _, s2 = make_engine(tmp_path / "w", score=0.9)
    eng2.create_twin(1)
    feed(eng2, s2, 60)
    assert eng2.get_twin(1).warning.state == "active" and eng2.get_twin(1).baseline.n_updates <= 1   # frozen while a warning is active
    eng3, _, s3 = make_engine(tmp_path / "off", "baseline.enabled=false")
    eng3.create_twin(1)
    feed(eng3, s3, 60)
    assert eng3.get_twin(1).baseline.status == "disabled" and eng3.get_twin(1).baseline.n_updates == 0
