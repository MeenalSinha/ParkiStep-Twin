"""Stable inference interface: a trailing sensor window (+ optional clinical record) -> probability that a new
freezing episode starts within the horizon. Uses only the supplied past window; reproduces the training pipeline."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from .features import causal_fill, position_quality, spec_from_dict, window_features, windows_from_signal


class InferenceInputError(ValueError):
    """The supplied window/clinical record is incompatible with the model's feature schema."""


@dataclass
class ForecastResult:
    probability: float | None
    horizon_s: float
    prediction_time: float
    window_start_time: float
    model_id: str
    experiment: str
    data_quality: str                     # ok | insufficient_sensor_quality
    warnings: list[str] = field(default_factory=list)
    features_expected: int = 0
    features_finite: int = 0
    used_clinical: bool = False
    threshold: float | None = None
    above_threshold: bool | None = None
    metadata: dict = field(default_factory=dict)
    disclaimer: str = "Research model score; not a diagnosis and not a validated clinical warning."

    def to_dict(self) -> dict:
        return asdict(self)


class ForecastPredictor:
    def __init__(self, manifest: dict, pipeline):
        self.manifest = manifest; self.pipeline = pipeline
        fm = manifest["feature_manifest"]
        self.spec = spec_from_dict(manifest["feature_spec"], manifest["channel_meta"])
        self.sensor_cols = fm["sensor_features"]
        self.clin_num, self.clin_cat = fm["clinical_numeric"], fm["clinical_categorical"]
        self.horizon = float(manifest["horizon_s"]); self.win = manifest["window"]

    @classmethod
    def load(cls, model_dir: str | Path, name: str) -> "ForecastPredictor":
        """`name` is the artifact stem, e.g. fusion__logistic__h3. The artifact is verified against its recorded SHA-256
        before loading; only load artifacts produced by your own training run."""
        model_dir = Path(model_dir)
        manifest = json.loads((model_dir / f"{name}.manifest.json").read_text())
        path = model_dir / manifest["artifact"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != manifest["sha256"]:
            raise InferenceInputError(f"artifact {path.name} does not match its recorded checksum; refusing to load")
        return cls(manifest, joblib.load(path))

    def predict(self, window: pd.DataFrame, timestamp_col: str, clinical: dict | None = None, metadata: dict | None = None) -> ForecastResult:
        m, W = self.manifest, self.win
        warns: list[str] = []
        missing = [c for c in [timestamp_col] + self.spec.raw_channels if c not in window.columns]
        if missing:
            raise InferenceInputError(f"window lacks required columns: {missing[:6]}{'...' if len(missing) > 6 else ''}")
        ts = window[timestamp_col].to_numpy(dtype=float)
        if np.isnan(ts).any() or (np.diff(ts) <= 0).any():
            raise InferenceInputError("timestamps must be finite and strictly increasing")
        if len(ts) < 2:
            raise InferenceInputError("window needs at least two samples")
        dt = float(np.median(np.diff(ts)))
        n = int(round(W["input_window_s"] / dt)) + 1
        if len(ts) < n:
            raise InferenceInputError(f"window has {len(ts)} samples but {n} are required for {W['input_window_s']} s")
        tail = window.iloc[-n:]
        tts = tail[timestamp_col].to_numpy(dtype=float)
        span, exp = tts[-1] - tts[0], (n - 1) * dt
        if abs(span - exp) > W["timestamp_tolerance_fraction"] * exp:
            raise InferenceInputError("trailing window contains a timestamp gap or irregular sampling; refusing to forecast across it")
        raw = tail[self.spec.raw_channels].to_numpy(dtype=float)
        filled = causal_fill(raw, int(W["max_ffill_samples"]))
        q = position_quality(raw, filled, np.array([n - 1]), n, self.spec, float(W["min_valid_fraction"]))
        bad = [p for p in m["sensor_positions"] if not q[p][0]]
        res = ForecastResult(None, self.horizon, float(tts[-1]), float(tts[0]), f"{m['experiment_id']}/{m['experiment']}/{m['model']}",
                             m["experiment"], "ok", warns, len(self.sensor_cols), 0, bool(m["uses_clinical"]), None, None, metadata or {})
        if bad:
            res.data_quality = "insufficient_sensor_quality"
            warns.append(f"sensor position(s) {bad} do not meet the required valid-data fraction; no forecast produced")
            return res
        feats = window_features(windows_from_signal(filled, np.array([n - 1]), n), self.spec, fs=1.0 / dt)[self.sensor_cols]
        row = feats.copy()
        if m["uses_clinical"]:
            clinical = clinical or {}
            for f, kind in m["clinical_source_fields"].items():
                col = "clin__" + f
                v = clinical.get(f)
                if v is None or (isinstance(v, float) and np.isnan(v)):
                    warns.append(f"clinical field {f!r} is missing; the trained imputation policy is applied")
                    row[col] = np.nan
                else:
                    if kind == "numeric":
                        try:
                            row[col] = float(v)
                        except (TypeError, ValueError):
                            raise InferenceInputError(f"clinical field {f!r} must be numeric, got {v!r}")
                    else:
                        lv = m["feature_manifest"]["categorical_levels"].get(col, [])
                        if str(v) not in lv:
                            warns.append(f"clinical field {f!r} has unseen category {v!r}; it is encoded as all-zero")
                        row[col] = str(v)
        elif clinical:
            warns.append("a clinical record was supplied but this model is sensor-only; it was ignored")
        cols = self.sensor_cols + self.clin_num + self.clin_cat
        X = row[cols] if m["uses_clinical"] else row[self.sensor_cols]
        p = self.pipeline.predict_proba(X)
        cls = list(self.pipeline.classes_)
        res.probability = float(p[0, cls.index(1)]) if 1 in cls else 0.0
        res.features_finite = int(np.isfinite(feats.to_numpy()).sum())
        if res.features_finite < res.features_expected:
            warns.append(f"{res.features_expected - res.features_finite} sensor features were not computable and were imputed by the trained policy")
        thr = m["threshold"].get("threshold")
        res.threshold = float(thr) if thr is not None else None
        res.above_threshold = bool(res.probability >= res.threshold) if res.threshold is not None else None
        return res
