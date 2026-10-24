"""SYNTHETIC fixtures and a TEST DOUBLE for Phase 3 unit tests. Nothing here is real FoG-STAR data and the
`StubPredictor` is NOT a trained model: it returns scripted scores and records exactly what it was given.
Integration tests that need a genuine trained model/real data live in test_phase3_real_model.py / test_phase3_real_data.py."""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

from parkistep.forecast.features import build_spec
from parkistep.forecast.inference import ForecastResult
from parkistep.twin.config import load_phase3
from parkistep.twin.engine import TwinEngine, observation_clock
from parkistep.twin.model import handle_from_predictor
from parkistep.twin.storage import StateStore

REPO = Path(__file__).resolve().parents[1]
DT = 0.1                      # synthetic sampling interval (10 Hz) - fast tests; the real data are 60 Hz
WINDOW_S = 1.0
STRIDE_S = 0.2
N_WIN = int(round(WINDOW_S / DT)) + 1


def settings(tmp_path, *overrides, mutate=None):
    """Load configs/phase3.yaml with test overrides. `mutate(cfg_dict)` can restructure the config (written to a temp file)."""
    ov = [f"persistence.path={tmp_path}/state.sqlite", f"outputs.dir={tmp_path}/out", "model.model=stub", "model.horizon_s=3.0",
          "buffer.max_duration_s=5.0", "baseline.min_updates=3", *overrides]
    path = REPO / "configs/phase3.yaml"
    if mutate is not None:
        import yaml
        raw = yaml.safe_load(path.read_text())
        mutate(raw["phase3"])
        path = Path(tmp_path) / "phase3_test.yaml"
        path.write_text(yaml.safe_dump(raw))
    return load_phase3(path, REPO, list(ov))


def make_manifest(s, *, uses_clinical=False, threshold=0.5, horizon=3.0, alerts=None, window_s=WINDOW_S, stride=STRIDE_S, dt=DT,
                  experiment="sensor_only", model="stub", thr_block=None):
    spec = build_spec(s.profile, s.phase2.cfg["features"])
    m = {"artifact": "stub.joblib", "sha256": "0" * 64, "experiment_id": "synthetic_test", "experiment": experiment, "model": model,
         "horizon_s": horizon, "window": {"input_window_s": window_s, "stride_s": stride, "timestamp_tolerance_fraction": 0.05,
                                          "min_valid_fraction": 0.95, "max_ffill_samples": 3},
         "sensor_positions": list(s.profile["sensor"]["channels"]["positions"]), "uses_clinical": uses_clinical,
         "feature_manifest": {"sensor_features": spec.names[:5], "clinical_numeric": [], "clinical_categorical": [],
                              "categorical_levels": {}, "raw_channels": spec.raw_channels},
         "feature_spec": spec.to_dict(), "channel_meta": {c: spec.channel_meta[c] for c in spec.raw_channels},
         "threshold": thr_block if thr_block is not None else {"policy": "fa_budget", "threshold": threshold},
         "alerts": alerts if alerts is not None else {"persistence_anchors": 1, "merge_gap_s": 1.0, "cooldown_s": 0.0,
                                                      "min_lead_s": 0.0, "extra_alerts_for_matched_event": "ignore"},
         "training": {"median_dt_s": dt, "fitted_on": "synthetic test double"}}
    if uses_clinical:
        m["clinical_source_fields"] = {"age": "numeric", "moca": "numeric", "gender": "categorical"}
    return m


class StubPredictor:
    """TEST DOUBLE. Scripted scores; records every call so tests can assert what the engine passed to the model."""

    def __init__(self, manifest, score=0.1, quality="ok"):
        self.manifest, self.score, self.quality, self.calls = manifest, score, quality, []

    def predict(self, window, timestamp_col, clinical=None, metadata=None):
        self.calls.append({"window": window.copy(), "clinical": None if clinical is None else dict(clinical), "metadata": metadata})
        ts = window[timestamp_col].to_numpy()
        p = float(self.score(float(ts[-1])) if callable(self.score) else self.score)
        m = self.manifest
        thr = (m["threshold"] or {}).get("threshold")
        return ForecastResult(p, float(m["horizon_s"]), float(ts[-1]), float(ts[0]), f"{m['experiment_id']}/{m['experiment']}/{m['model']}",
                              m["experiment"], self.quality, [], 5, 5, bool(m["uses_clinical"]), thr, (p >= thr) if thr is not None else None)


def make_engine(tmp_path, *overrides, score=0.1, uses_clinical=False, manifest_kw=None, persistence=False, clinical_source=None, clock=observation_clock, mutate=None):
    s = settings(tmp_path, *overrides, mutate=mutate)
    man = make_manifest(s, uses_clinical=uses_clinical, **(manifest_kw or {}))
    stub = StubPredictor(man, score)
    h = handle_from_predictor(stub, s)
    store = StateStore("sqlite", s.state_path, s.cfg["retention"]) if persistence else None
    return TwinEngine(s, handle=h, store=store, clinical_source=clinical_source, clock=clock), stub, s


def channels(s):
    return list(build_spec(s.profile, s.phase2.cfg["features"]).raw_channels)


def rec(s, i, patient=1, session="r1", nan_channels=(), dt=DT, t0=0.0, ts=None, seed=0):
    """One synthetic observation record for sample index i."""
    rng = np.random.default_rng(seed * 100003 + i)
    r = {"participant_id": patient, "timestamp_s": (t0 + i * dt) if ts is None else ts, "recording_id": session}
    for c in channels(s):
        r[c] = float("nan") if any(c.startswith(p) for p in nan_channels) else float(rng.normal())
    return r


def stream(s, n, **kw):
    return [rec(s, i, **kw) for i in range(n)]


def require_real(cond: bool, reason: str):
    """Real-data tests run when the dataset/artifacts exist. PARKISTEP_REQUIRE_REAL=1 turns a missing prerequisite into a failure."""
    if not cond:
        if os.environ.get("PARKISTEP_REQUIRE_REAL") == "1":
            pytest.fail("REQUIRED real prerequisite missing: " + reason)
        pytest.skip(reason)
