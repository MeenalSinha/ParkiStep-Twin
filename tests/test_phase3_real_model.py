"""Engine integration with GENUINE trained Phase 2 artifacts (real sklearn pipelines, real ForecastPredictor, real checksums).
The training DATA here is synthetic (fast, self-contained); real FoG-STAR data/artifacts are exercised in test_phase3_real_data.py."""
import json
import shutil

import numpy as np
import pandas as pd
import pytest

from parkistep.forecast import experiment as ex
from parkistep.forecast.inference import ForecastPredictor
from parkistep.forecast.prepare import run_prepare
from parkistep.forecast.settings import load_phase2
from parkistep.twin.engine import TwinEngine, observation_clock
from parkistep.twin.errors import ModelUnavailableError
from parkistep.twin.model import load_model
from p2helpers import REPO, synthetic_real_schema
from p3helpers import settings


@pytest.fixture(scope="module")
def trained(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("p3_real_model")
    sensor, clin = synthetic_real_schema()
    ov = [f"outputs.artifacts_root={tmp}/art", f"outputs.reports_dir={tmp}/rep", f"outputs.window_cache_dir={tmp}/cache",
          "windowing.horizons_s=[1.0]", "split.n_splits=3", "evaluation.bootstrap.n_resamples=20", "threshold.inner_splits=2",
          "models={logistic: {type: logistic_regression, params: {C: 0.05, max_iter: 300, class_weight: balanced}}}",
          "evaluation.selection.among=[logistic]"]
    s2 = load_phase2(REPO / "configs/phase2.yaml", REPO, ov)
    run_prepare(s2, sensor=sensor, clinical=clin, force=True)
    ex.train(s2, force=True)
    return s2.artifact_dir / "models", sensor, clin


def p3(tmp_path, models_dir, experiment, *extra):
    return settings(tmp_path, f"model.artifact_dir={models_dir}", f"model.experiment={experiment}", "model.model=logistic",
                    "model.horizon_s=1.0", "buffer.max_duration_s=10.0", *extra)


def records(sensor, rid, cols, n=None):
    g = sensor[sensor.recording_id == rid]
    g = g.iloc[:n] if n else g
    return g[["participant_id", "timestamp_s", "recording_id", *cols]].to_dict("records")


def clinical_row(clin, pid):
    return clin[clin.participant_id == pid].iloc[0].to_dict()


def test_engine_loads_a_verified_genuine_artifact(trained, tmp_path):
    models, _, _ = trained
    s = p3(tmp_path, models, "sensor_only")
    h = load_model(s)
    man = json.loads((models / "sensor_only__logistic__h1.manifest.json").read_text())
    assert h.available and h.artifact_sha256 == man["sha256"] and h.horizon_s == 1.0 and h.uses_clinical is False
    assert h.threshold.source == "model_manifest" and h.threshold.value == man["threshold"]["threshold"]
    r = h.requirements
    assert r.input_window_s == man["window"]["input_window_s"] and r.n_samples == int(round(r.input_window_s / man["training"]["median_dt_s"])) + 1
    assert r.raw_channels == tuple(man["feature_spec"]["raw_channels"]) and h.alert_policy == man["alerts"]


def test_corrupted_artifact_is_refused_and_degrade_mode_reports_it(trained, tmp_path):
    models, _, _ = trained
    bad = tmp_path / "models"
    shutil.copytree(models, bad)
    art = bad / "sensor_only__logistic__h1.joblib"
    art.write_bytes(art.read_bytes() + b"tamper")                  # checksum no longer matches the manifest
    s = p3(tmp_path, bad, "sensor_only")
    with pytest.raises(ModelUnavailableError, match="(?i)sha|checksum|mismatch"):
        load_model(s)
    s2 = p3(tmp_path / "d", bad, "sensor_only", "model.on_unavailable=degrade")
    h = load_model(s2)
    assert not h.available and h.problems
    eng = TwinEngine(s2, handle=h, clock=observation_clock)
    assert eng.create_twin(1).status == "MODEL_UNAVAILABLE"


def test_manifest_without_artifact_is_a_failed_gate(trained, tmp_path):
    models, _, _ = trained
    bad = tmp_path / "models"
    shutil.copytree(models, bad)
    (bad / "sensor_only__logistic__h1.joblib").unlink()
    with pytest.raises(ModelUnavailableError, match="missing"):
        load_model(p3(tmp_path, bad, "sensor_only"))


def test_unavailable_horizon_is_never_substituted(trained, tmp_path):
    models, _, _ = trained
    s = settings(tmp_path, f"model.artifact_dir={models}", "model.experiment=sensor_only", "model.model=logistic", "model.horizon_s=2.0")
    with pytest.raises(ModelUnavailableError, match="manifest not found"):
        load_model(s)


def test_engine_forecasts_match_direct_predictor_calls_on_the_same_trailing_window(trained, tmp_path):
    models, sensor, clin = trained
    s = p3(tmp_path, models, "sensor_only")
    eng = TwinEngine(s, clock=observation_clock)
    h = eng.handle
    rid = "p1-s1-t1"
    eng.create_twin(1)
    recs = records(sensor, rid, h.requirements.raw_channels, 400)
    for r in recs:
        eng.ingest(1, r)
    preds = eng.recent_predictions(1, 9999)
    assert len(preds) > 20
    direct = ForecastPredictor.load(models, "sensor_only__logistic__h1")
    g = sensor[sensor.recording_id == rid].reset_index(drop=True)
    n = h.requirements.n_samples
    for p in preds[::7]:
        end = int(np.argmin(np.abs(g.timestamp_s.to_numpy() - p.window_end_time)))
        win = g.iloc[end - n + 1:end + 1][["timestamp_s", *h.requirements.raw_channels]]
        d = direct.predict(win, "timestamp_s")
        assert d.probability == pytest.approx(p.probability, abs=1e-12) and d.prediction_time == pytest.approx(p.prediction_time)
        assert p.threshold == d.threshold and p.above_threshold == d.above_threshold and p.horizon_s == d.horizon_s
    assert not [e for e in eng.event_history(1, "FORECAST_UNAVAILABLE") if e.detail["reason"] == "predictor_rejected_input"]


def test_engine_and_predictor_never_disagree_about_window_validity(trained, tmp_path):
    models, sensor, clin = trained
    s = p3(tmp_path, models, "sensor_only")
    eng = TwinEngine(s, clock=observation_clock)
    eng.create_twin(1)
    cols = eng.handle.requirements.raw_channels
    rng = np.random.default_rng(3)
    for i, r in enumerate(records(sensor, "p1-s1-t1", cols, 700)):
        if 200 <= i < 215 or 450 <= i < 452:
            r = {**r, **{c: float("nan") for c in cols if c.startswith("wrist")}}      # sensor dropout bursts
        if i == 300:
            r = {**r, "ankleL_acc_x": float("nan")}                                    # isolated sample
        eng.ingest(1, r)
    reasons = {e.detail["reason"] for e in eng.event_history(1, "FORECAST_UNAVAILABLE")}
    assert "predictor_rejected_input" not in reasons and "insufficient_sensor_quality" in reasons
    assert eng.get_twin(1).stream.n_accepted_total == 700 and eng.get_twin(1).forecast.n_predictions > 20


def test_sensor_only_forecast_ignores_clinical_data_and_says_so(trained, tmp_path):
    models, sensor, clin = trained
    eng = TwinEngine(p3(tmp_path, models, "sensor_only"), clock=observation_clock)
    cols = eng.handle.requirements.raw_channels
    a = eng.create_twin(1, clinical=clinical_row(clin, 1))
    b = eng.create_twin(2, clinical=clinical_row(clin, 2) | {"age": 99.0})
    recs = records(sensor, "p1-s1-t1", cols, 150)
    for r in recs:
        eng.ingest(1, r)
        eng.ingest(2, {**r, "participant_id": 2})                     # SAME sensor data, very different clinical record
    pa, pb = eng.recent_predictions(1, 999), eng.recent_predictions(2, 999)
    assert len(pa) == len(pb) > 5
    assert [x.probability for x in pa] == [x.probability for x in pb]
    assert all(x.clinical_used is False and x.uses_clinical is False for x in pa)
    assert eng.get_twin(1).clinical.influences_forecast is False and a.clinical.values["age"] != b.clinical.values["age"]


def test_fusion_forecast_depends_on_the_patients_clinical_record(trained, tmp_path):
    models, sensor, clin = trained
    eng = TwinEngine(p3(tmp_path, models, "fusion"), clock=observation_clock)
    h = eng.handle
    assert h.uses_clinical and set(h.clinical_fields) >= {"age", "gender"}
    cols = h.requirements.raw_channels
    eng.create_twin(1, clinical=clinical_row(clin, 1))
    eng.create_twin(2, clinical=clinical_row(clin, 2) | {"age": 20.0, "updrs_iii": 70.0, "fog_q": 1.0})
    for r in records(sensor, "p1-s1-t1", cols, 150):
        eng.ingest(1, r)
        eng.ingest(2, {**r, "participant_id": 2})
    pa, pb = eng.recent_predictions(1, 999), eng.recent_predictions(2, 999)
    assert len(pa) == len(pb) > 5 and all(x.clinical_used and x.uses_clinical for x in pa)
    assert [x.probability for x in pa] != [x.probability for x in pb]            # clinical input genuinely entered the forecast
    assert eng.get_twin(1).clinical.influences_forecast is True


def test_fusion_missing_clinical_value_follows_the_saved_policy_and_is_reported(trained, tmp_path):
    models, sensor, clin = trained
    eng = TwinEngine(p3(tmp_path, models, "fusion"), clock=observation_clock)
    cols = eng.handle.requirements.raw_channels
    row = clinical_row(clin, 1)
    row["updrs_iii"] = None                                         # a documented-missing value, never invented by the engine
    st = eng.create_twin(1, clinical=row)
    assert "updrs_iii" in st.clinical.missing_fields and st.clinical.values["updrs_iii"] is None
    for r in records(sensor, "p1-s1-t1", cols, 120):
        eng.ingest(1, r)
    p = eng.latest_prediction(1)
    assert p is not None and 0.0 <= p.probability <= 1.0 and any("updrs_iii" in w for w in p.warnings), p.warnings


def test_require_all_blocks_a_fusion_forecast_with_missing_clinical_value(trained, tmp_path):
    models, sensor, clin = trained
    eng = TwinEngine(p3(tmp_path, models, "fusion", "patient.clinical_missing_policy=require_all"), clock=observation_clock)
    cols = eng.handle.requirements.raw_channels
    eng.create_twin(1, clinical=clinical_row(clin, 1) | {"updrs_iii": None})
    for r in records(sensor, "p1-s1-t1", cols, 120):
        eng.ingest(1, r)
    assert eng.latest_prediction(1) is None and eng.get_twin(1).status == "DEGRADED_DATA_QUALITY"


def test_state_persistence_roundtrip_with_a_genuine_model(trained, tmp_path):
    models, sensor, clin = trained
    s = p3(tmp_path, models, "sensor_only")
    from parkistep.twin.service import build_engine
    eng = build_engine(s, clinical_source=None, clock=observation_clock)
    cols = eng.handle.requirements.raw_channels
    eng.create_twin(1)
    for r in records(sensor, "p1-s1-t1", cols, 300):
        eng.ingest(1, r)
    before = [(p.prediction_id, p.probability) for p in eng.recent_predictions(1, 9999)]
    eng.close()
    eng2 = build_engine(s, clinical_source=None, clock=observation_clock)
    st = eng2.restore_twin(1)
    assert [(p.prediction_id, p.probability) for p in eng2.recent_predictions(1, 9999)] == before
    assert st.forecast.status == "historical" and st.status == "WAITING_FOR_DATA"
    eng2.close()
