"""End-to-end run on SYNTHETIC data with the real canonical schema: prepare -> train -> evaluate -> inference."""
import hashlib
import json

import joblib
import numpy as np
import pandas as pd
import pytest

from parkistep.forecast import experiment as ex
from parkistep.forecast.evaluate import window_metrics
from parkistep.forecast.inference import ForecastPredictor, InferenceInputError
from parkistep.forecast.prepare import run_prepare
from parkistep.forecast.settings import load_phase2
from p2helpers import REPO, synthetic_real_schema


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("p2")
    sensor, clin = synthetic_real_schema()
    ov = [f"outputs.artifacts_root={tmp}/art", f"outputs.reports_dir={tmp}/rep", f"outputs.window_cache_dir={tmp}/cache",
          "windowing.horizons_s=[1.0, 2.0]", "split.n_splits=3", "evaluation.bootstrap.n_resamples=30", "threshold.inner_splits=2",
          "models={dummy_prior: {type: dummy_prior}, dummy_random: {type: dummy_random}, logistic: {type: logistic_regression, params: {C: 0.05, max_iter: 300, class_weight: balanced}}}",
          "evaluation.selection.among=[logistic]"]
    s = load_phase2(REPO / "configs/phase2.yaml", REPO, ov)
    run_prepare(s, sensor=sensor, clinical=clin, force=True)
    ex.train(s, force=True)
    ex.evaluate(s)
    return s, sensor, clin


def test_event_table_matches_annotations(run):
    s, sensor, _ = run
    ev = pd.read_parquet(s.cache_dir / "events.parquet")
    assert len(ev) == 2 * 2 * sum(1 for p in range(1, 13) if p % 4 != 0)
    assert set(ev.columns) >= {"event_id", "recording_id", "participant_id", "session_id", "task_id", "onset_t", "end_t"}


def test_oof_predictions_are_participant_grouped_and_traceable(run):
    s, *_ = run
    for f in (s.artifact_dir / "oof").glob("*.parquet"):
        d = pd.read_parquet(f)
        assert d.groupby("participant_id")["fold"].nunique().max() == 1                      # a participant is tested in exactly one fold
        assert {"window_id", "recording_id", "participant_id", "anchor_t", "event_id", "y", "prob", "threshold"} <= set(d.columns)
        assert d.window_id.is_unique and d.prob.between(0, 1).all() and np.isfinite(d.prob).all()
    sm = pd.read_csv(s.artifact_dir / "split_manifest.csv")
    assert sm.participant_id.is_unique and sm.fold.nunique() == 3


def test_snapshots_and_metadata_saved(run):
    s, *_ = run
    snap = json.loads((s.artifact_dir / "config_snapshot.json").read_text())
    assert snap["config_hash"] == s.config_hash and snap["phase2"]["windowing"]["horizons_s"] == [1.0, 2.0]
    assert "scikit-learn" in json.loads((s.artifact_dir / "software_versions.json").read_text())
    assert (s.artifact_dir / "dataset_provenance.json").exists() and (s.artifact_dir / "train.log").exists()


def test_signal_is_learnable_and_reports_are_from_computed_results(run):
    s, *_ = run
    m = json.loads((s.report_dir / "phase2_metrics.json").read_text())
    r = m["results"]["sensor_only|logistic|h1"]
    assert r["window"]["average_precision"] > 2 * r["window"]["prevalence"]
    assert r["event"]["n_events_evaluable"] > 0 and r["event"]["event_sensitivity"] is not None
    d = pd.read_parquet(s.artifact_dir / "oof" / "sensor_only__logistic__h1.parquet")
    assert r["window"]["n_windows"] == len(d) and r["window"]["n_positive"] == int(d.y.sum())
    rep = (s.report_dir / "phase2_results_report.md").read_text()
    assert "sensor_only" in rep and "fusion" in rep and f"{r['window']['n_windows']}" in rep
    assert len(m["comparison"]) == 2 * 1 + 0 or len(m["comparison"]) >= 2                   # logistic x 2 horizons (+dummies)


def test_fusion_and_sensor_only_share_participants_folds_and_events(run):
    s, *_ = run
    a = pd.read_parquet(s.artifact_dir / "oof" / "sensor_only__logistic__h1.parquet")
    b = pd.read_parquet(s.artifact_dir / "oof" / "fusion__logistic__h1.parquet")
    fa = a.groupby("participant_id").fold.first(); fb = b.groupby("participant_id").fold.first()
    assert fa.equals(fb) and set(a.window_id) == set(b.window_id)


def test_dummy_baseline_has_no_skill(run):
    s, *_ = run
    m = json.loads((s.report_dir / "phase2_metrics.json").read_text())["results"]["sensor_only|dummy_prior|h1"]
    assert m["window"]["average_precision"] == pytest.approx(m["window"]["prevalence"], abs=1e-6)


def test_feature_manifest_excludes_labels_and_identifiers(run):
    s, *_ = run
    for f in (s.artifact_dir / "models").glob("*.manifest.json"):
        man = json.loads(f.read_text())
        names = man["feature_manifest"]["sensor_features"] + man["feature_manifest"]["clinical_numeric"] + man["feature_manifest"]["clinical_categorical"]
        forbidden = {"fog", "fog_severity", "clin__fog", "clin__fog_severity", "participant_id", "clin__participant_id"}
        assert not [n for n in names if n in forbidden or n.startswith(("fog__", "event", "onset"))]
        assert not [n for n in man["feature_manifest"]["sensor_features"] if any(b in n.lower() for b in ("fog", "participant", "session", "task", "activity", "timestamp", "event", "onset"))]
        assert (man["uses_clinical"]) == (man["experiment"] == "fusion")
        assert man["threshold"].get("threshold") is not None and man["sha256"]


def _window_for(s, sensor, rid, idx, man):
    n = int(round(man["window"]["input_window_s"] * 30.0)) + 1
    g = sensor[sensor.recording_id == rid].reset_index(drop=True)
    return g.iloc[idx - n + 1: idx + 1]


@pytest.mark.parametrize("name,uses_clin", [("sensor_only__logistic__h1", False), ("fusion__logistic__h1", True)])
def test_saved_model_reloads_and_reproduces_training_pipeline_output(run, name, uses_clin):
    s, sensor, clin = run
    pred = ForecastPredictor.load(s.artifact_dir / "models", name)
    anchors = pd.read_parquet(s.cache_dir / "anchors.parquet"); feats = pd.read_parquet(s.cache_dir / "features.parquet")
    oof = pd.read_parquet(s.artifact_dir / "oof" / f"{name}.parquet")
    row = oof.iloc[len(oof) // 2]
    a = anchors[anchors.window_id == row.window_id].iloc[0]
    win = _window_for(s, sensor, a.recording_id, int(a.anchor_idx), pred.manifest)
    c = clin[clin.participant_id == a.participant_id].iloc[0].to_dict() if uses_clin else None
    res = pred.predict(win, "timestamp_s", clinical=c, metadata={"recording_id": a.recording_id})
    assert res.data_quality == "ok" and res.horizon_s == 1.0 and res.prediction_time == pytest.approx(a.anchor_t)
    assert res.metadata["recording_id"] == a.recording_id and 0 <= res.probability <= 1
    # same pipeline, features recomputed from the raw window == features cached at training time
    fcols = pred.sensor_cols
    cached = feats[feats.window_id == a.window_id][fcols].iloc[0].to_numpy(dtype=float)
    from parkistep.forecast.features import window_features, windows_from_signal, causal_fill
    raw = causal_fill(win[pred.spec.raw_channels].to_numpy(float), 3)
    again = window_features(windows_from_signal(raw, np.array([len(win) - 1]), len(win)), pred.spec, 30.0)[fcols].iloc[0].to_numpy(float)
    np.testing.assert_allclose(again, cached, rtol=1e-4, atol=1e-5, equal_nan=True)
    assert res.used_clinical == uses_clin


def test_inference_rejects_incompatible_inputs_and_tampered_artifacts(run, tmp_path):
    s, sensor, clin = run
    pred = ForecastPredictor.load(s.artifact_dir / "models", "fusion__logistic__h1")
    win = sensor[sensor.recording_id == "p1-s1-t1"].iloc[100:100 + 61]
    with pytest.raises(InferenceInputError, match="lacks required columns"):
        pred.predict(win.drop(columns=["back_acc_x"]), "timestamp_s")
    with pytest.raises(InferenceInputError, match="samples"):
        pred.predict(win.iloc[:20], "timestamp_s")
    gap = win.copy(); gap.iloc[30:, gap.columns.get_loc("timestamp_s")] += 0.5
    with pytest.raises(InferenceInputError, match="gap"):
        pred.predict(gap, "timestamp_s")
    r = pred.predict(win, "timestamp_s", clinical={"age": 70, "gender": "X"})                # missing fields -> trained imputation, with warnings
    assert any("missing" in w for w in r.warnings) and any("unseen category" in w for w in r.warnings)
    with pytest.raises(InferenceInputError, match="numeric"):
        pred.predict(win, "timestamp_s", clinical={"age": "old"})
    so = ForecastPredictor.load(s.artifact_dir / "models", "sensor_only__logistic__h1")
    r2 = so.predict(win, "timestamp_s", clinical={"age": 70})
    assert not r2.used_clinical and any("sensor-only" in w for w in r2.warnings)
    bad = win.copy(); bad[[c for c in bad.columns if c.startswith("wrist_")]] = np.nan
    assert so.predict(bad, "timestamp_s").data_quality == "insufficient_sensor_quality"
    import shutil
    shutil.copytree(s.artifact_dir / "models", tmp_path / "m")
    with open(tmp_path / "m" / "sensor_only__logistic__h1.joblib", "ab") as f:
        f.write(b"x")
    with pytest.raises(InferenceInputError, match="checksum"):
        ForecastPredictor.load(tmp_path / "m", "sensor_only__logistic__h1")


def test_metrics_without_positives_are_not_invented():
    m = window_metrics(np.zeros(10, int), np.linspace(0, 1, 10), np.full(10, 0.5))
    assert m["average_precision"] is None and m["roc_auc"] is None and m["at_selected_thresholds"]["recall"] is None
