"""Gateway to the Phase 2 forecasting model.

The Phase 2 manifest is the single source of truth for the input window, the feature schema, the horizon and the
operating threshold. This module reads those values (it does not restate them), checks the Phase 3 configuration
against them, and refuses incompatible combinations instead of substituting another model.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

from ..forecast.features import spec_from_dict
from ..forecast.inference import ForecastPredictor, InferenceInputError
from ..forecast.windows import horizon_key
from .config import Phase3Settings
from .errors import ModelUnavailableError

log = logging.getLogger(__name__)

REQUIRED_MANIFEST_KEYS = ("artifact", "sha256", "experiment_id", "experiment", "model", "horizon_s", "window",
                          "sensor_positions", "uses_clinical", "feature_manifest", "feature_spec", "channel_meta",
                          "threshold", "alerts", "training")
REQUIRED_WINDOW_KEYS = ("input_window_s", "stride_s", "timestamp_tolerance_fraction", "min_valid_fraction", "max_ffill_samples")


@dataclass(frozen=True)
class WindowRequirements:
    """What a trailing window must satisfy before the model may be called (all derived from the manifest)."""
    input_window_s: float
    dt_s: float
    n_samples: int
    timestamp_tolerance_fraction: float
    min_valid_fraction: float
    max_ffill_samples: int
    positions: tuple
    raw_channels: tuple
    stride_s: float
    timestamp_col: str

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["positions"], d["raw_channels"] = list(self.positions), list(self.raw_channels)
        return d


@dataclass(frozen=True)
class ThresholdInfo:
    value: float | None
    source: str                      # model_manifest | configured_fallback | unavailable
    policy: dict = field(default_factory=dict)
    note: str | None = None

    def to_dict(self) -> dict:
        return {"value": self.value, "source": self.source, "policy": dict(self.policy), "note": self.note}


@dataclass
class ModelHandle:
    status: str                                  # available | unavailable
    problems: list = field(default_factory=list)
    predictor: object | None = None
    manifest: dict | None = None
    model_id: str | None = None
    artifact_name: str | None = None
    artifact_sha256: str | None = None
    horizon_s: float | None = None
    uses_clinical: bool = False
    experiment: str | None = None
    requirements: WindowRequirements | None = None
    threshold: ThresholdInfo = field(default_factory=lambda: ThresholdInfo(None, "unavailable"))
    alert_policy: dict = field(default_factory=dict)       # the alert policy saved with the model (Phase 2)
    clinical_fields: dict = field(default_factory=dict)    # model clinical field -> numeric|categorical
    cadence_s: float | None = None
    spec: object | None = None                              # Phase 2 FeatureSpec rebuilt from the manifest

    @property
    def available(self) -> bool:
        return self.status == "available"

    def identity(self) -> dict:
        return {"model_id": self.model_id, "artifact": self.artifact_name, "sha256": self.artifact_sha256,
                "horizon_s": self.horizon_s, "experiment": self.experiment, "uses_clinical": self.uses_clinical}


def unavailable(problems: list[str]) -> ModelHandle:
    return ModelHandle(status="unavailable", problems=list(problems))


def artifact_stem(experiment: str, model: str, horizon_s: float) -> str:
    return f"{experiment}__{model}__{horizon_key(horizon_s)}"


def _check_manifest(manifest: dict, s: Phase3Settings, stem: str) -> list[str]:
    cfg, prof = s.cfg, s.profile
    mc, problems = cfg["model"], []
    miss = [k for k in REQUIRED_MANIFEST_KEYS if k not in manifest]
    if miss:
        return [f"manifest lacks required keys {miss}"]
    miss = [k for k in REQUIRED_WINDOW_KEYS if k not in manifest["window"]]
    if miss:
        return [f"manifest window lacks keys {miss}"]
    if manifest["experiment"] != mc["experiment"] or manifest["model"] != mc["model"]:
        problems.append(f"manifest is for {manifest['experiment']}/{manifest['model']} but the configuration selects "
                        f"{mc['experiment']}/{mc['model']}")
    if abs(float(manifest["horizon_s"]) - float(mc["horizon_s"])) > 1e-9:
        problems.append(f"configured horizon {mc['horizon_s']} s does not match the artifact's horizon "
                        f"{manifest['horizon_s']} s (no silent substitution)")
    positions = set(prof["sensor"]["channels"]["positions"])
    if set(manifest["sensor_positions"]) - positions:
        problems.append(f"model needs sensor positions {sorted(set(manifest['sensor_positions']) - positions)} that the dataset profile lacks")
    raw = list(manifest["feature_spec"]["raw_channels"])
    ch = cfg["observation"]["channels"]
    provided = set(raw) if ch["mode"] == "identity" else set(ch["mapping"].values())
    if set(raw) - provided:
        problems.append(f"observation channel configuration does not provide model channels {sorted(set(raw) - provided)[:5]}")
    if not manifest["feature_manifest"].get("sensor_features"):
        problems.append("manifest has an empty sensor feature schema")
    if manifest["uses_clinical"]:
        fields = manifest.get("clinical_source_fields") or {}
        if not fields:
            problems.append("fusion model manifest has no clinical_source_fields")
        mapping = cfg["patient"].get("clinical_feature_mapping") or {}
        for f in fields:
            src = mapping.get(f, f)
            if src not in prof["clinical"]["fields"]:
                problems.append(f"model clinical field {f!r} maps to {src!r}, which is not a clinical field of the dataset profile")
    W = manifest["window"]
    mvf = cfg["buffer"].get("min_valid_fraction")
    if mvf is not None and mvf < float(W["min_valid_fraction"]) - 1e-12:
        problems.append(f"buffer.min_valid_fraction={mvf} is weaker than the model's own requirement {W['min_valid_fraction']}")
    return problems


def _build_requirements(manifest: dict, s: Phase3Settings) -> tuple[WindowRequirements, float, list[str]]:
    cfg, mc, problems = s.cfg, s.cfg["model"], []
    W = manifest["window"]
    if mc["expected_dt_source"] == "config":
        dt = float(mc["expected_dt_s"])
    else:
        dt = float(manifest["training"].get("median_dt_s") or 0.0)
        if dt <= 0:
            problems.append("manifest has no training.median_dt_s; set model.expected_dt_source=config and model.expected_dt_s")
            dt = 1.0
    n = int(round(float(W["input_window_s"]) / dt)) + 1            # same rule as Phase 2 windows/inference
    mvf = max(float(W["min_valid_fraction"]), float(cfg["buffer"].get("min_valid_fraction") or 0.0))
    req = WindowRequirements(float(W["input_window_s"]), dt, n, float(W["timestamp_tolerance_fraction"]), mvf,
                             int(W["max_ffill_samples"]), tuple(manifest["sensor_positions"]),
                             tuple(manifest["feature_spec"]["raw_channels"]), float(W["stride_s"]),
                             s.profile["sensor"]["timestamp"]["canonical"])
    b = cfg["buffer"]
    need_s = req.input_window_s + float(b["warmup_extra_s"])
    if float(b["max_duration_s"]) < need_s - 1e-9:
        problems.append(f"buffer.max_duration_s={b['max_duration_s']} is shorter than the model window plus warm-up ({need_s} s)")
    if int(b["max_observations"]) < n:
        problems.append(f"buffer.max_observations={b['max_observations']} is smaller than the {n} samples the model window needs")
    cad = cfg["cadence"].get("every_s")
    cadence = float(cad) if cad else req.stride_s
    if cadence < dt - 1e-12:
        problems.append(f"inference cadence {cadence} s is shorter than the sampling interval {dt} s")
    return req, cadence, problems


def _threshold(manifest: dict, s: Phase3Settings) -> ThresholdInfo:
    t = manifest.get("threshold") or {}
    v = t.get("threshold")
    ok = isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
    if ok:
        note = "threshold exceeds 1.0: this model's alert can never trigger" if v > 1.0 else t.get("note")
        return ThresholdInfo(float(v), "model_manifest", dict(t), note)
    fb = s.cfg["warning"]["threshold"].get("fallback_value")
    if fb is not None:
        return ThresholdInfo(float(fb), "configured_fallback", dict(t), "manifest has no valid operating threshold; configured fallback used")
    return ThresholdInfo(None, "unavailable", dict(t), "manifest has no valid operating threshold; threshold-based warnings are unavailable")


def handle_from_predictor(predictor, s: Phase3Settings, stem: str | None = None) -> ModelHandle:
    """Wrap an already constructed predictor (real or - in unit tests only - a stub) after the same compatibility checks."""
    manifest = predictor.manifest
    probs = _check_manifest(manifest, s, stem or "")
    if probs:
        raise ModelUnavailableError("; ".join(probs))
    req, cadence, probs = _build_requirements(manifest, s)
    if probs:
        raise ModelUnavailableError("; ".join(probs))
    return ModelHandle(status="available", predictor=predictor, manifest=manifest,
                       model_id=f"{manifest['experiment_id']}/{manifest['experiment']}/{manifest['model']}",
                       artifact_name=manifest["artifact"], artifact_sha256=manifest["sha256"],
                       horizon_s=float(manifest["horizon_s"]), uses_clinical=bool(manifest["uses_clinical"]),
                       experiment=manifest["experiment"], requirements=req, threshold=_threshold(manifest, s),
                       alert_policy=dict(manifest.get("alerts") or {}), clinical_fields=dict(manifest.get("clinical_source_fields") or {}),
                       cadence_s=cadence, spec=spec_from_dict(manifest["feature_spec"], manifest["channel_meta"]))


def load_model(s: Phase3Settings) -> ModelHandle:
    """Load the configured artifact through ForecastPredictor (which verifies its SHA-256).

    on_unavailable = fail    -> raise ModelUnavailableError
    on_unavailable = degrade -> return a handle with status 'unavailable' and the reasons (the engine then runs without forecasts)
    """
    mc = s.cfg["model"]
    stem = artifact_stem(mc["experiment"], mc["model"], float(mc["horizon_s"]))
    mdir = s.model_dir
    try:
        mpath = mdir / f"{stem}.manifest.json"
        if not mpath.is_file():
            avail = sorted(p.name[:-len(".manifest.json")] for p in mdir.glob("*.manifest.json")) if mdir.is_dir() else []
            raise ModelUnavailableError(f"model manifest not found: {mpath}. Available artifacts in {mdir}: {avail or 'none'}")
        manifest = json.loads(mpath.read_text())
        art = mdir / str(manifest.get("artifact", ""))
        if not art.is_file():
            raise ModelUnavailableError(f"manifest {mpath.name} exists but its model file {art.name} is missing (manifest without artifact)")
        predictor = ForecastPredictor.load(mdir, stem)
        h = handle_from_predictor(predictor, s, stem)
        log.info("model loaded: %s (horizon %.1f s, uses_clinical=%s, threshold=%s [%s])", h.model_id, h.horizon_s,
                 h.uses_clinical, h.threshold.value, h.threshold.source)
        return h
    except (ModelUnavailableError, InferenceInputError, json.JSONDecodeError, OSError, KeyError) as e:
        msg = f"{type(e).__name__}: {e}" if not isinstance(e, ModelUnavailableError) else str(e)
        if mc["on_unavailable"] == "degrade":
            log.error("model unavailable (degrade mode): %s", msg)
            return unavailable([msg])
        raise (e if isinstance(e, ModelUnavailableError) else ModelUnavailableError(msg)) from e
