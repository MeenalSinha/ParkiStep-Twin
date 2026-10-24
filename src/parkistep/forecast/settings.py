"""Phase 2 configuration: loading, validation, hashing, snapshots. No experiment value lives in code."""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import ConfigError, Settings, apply_overrides, load_settings, load_yaml

MODEL_TYPES = ("dummy_prior", "dummy_random", "logistic_regression", "random_forest")
TIME_FEATURES = ("mean", "std", "min", "max", "rms", "q25", "median", "q75", "range", "mean_abs_diff")
FREQ_FEATURES = ("dominant_freq", "band_energy", "band_ratio", "spectral_entropy", "total_power")
THRESHOLD_POLICIES = ("fa_budget", "max_fbeta", "fixed")
SPLIT_STRATEGIES = ("balanced_group_kfold", "leave_one_group_out")


@dataclass
class Phase2Settings:
    cfg: dict                 # the validated `phase2` section
    base: Settings            # Phase 1 settings (dataset profile, paths)
    root: Path
    config_path: Path

    def __getitem__(self, k):
        return self.cfg[k]

    def resolve(self, p) -> Path:
        p = Path(p)
        return p if p.is_absolute() else self.root / p

    @property
    def experiment_id(self) -> str:
        return f"{self.cfg['name']}_{self.config_hash[:8]}"

    @property
    def config_hash(self) -> str:
        return hashlib.sha256(json.dumps(self.cfg, sort_keys=True, default=str).encode()).hexdigest()

    @property
    def artifact_dir(self) -> Path:
        return self.resolve(self.cfg["outputs"]["artifacts_root"]) / self.experiment_id

    @property
    def report_dir(self) -> Path:
        return self.resolve(self.cfg["outputs"]["reports_dir"])

    @property
    def cache_dir(self) -> Path:
        # windows/features depend only on data+windowing+features config, so they get their own hash
        sub = {k: self.cfg[k] for k in ("data", "events", "windowing", "features")}
        h = hashlib.sha256(json.dumps(sub, sort_keys=True, default=str).encode()).hexdigest()[:8]
        return self.resolve(self.cfg["outputs"]["window_cache_dir"]) / f"windows_{h}"

    def snapshot(self) -> dict:
        return {"phase2": copy.deepcopy(self.cfg), "config_hash": self.config_hash, "experiment_id": self.experiment_id,
                "config_file": str(self.config_path)}


def _need(cfg, dotted, problems):
    cur = cfg
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            problems.append(f"missing required setting `phase2.{dotted}`")
            return None
        cur = cur[part]
    return cur


def validate_phase2(cfg: dict, profile: dict) -> None:
    p: list[str] = []
    for key in ("data", "events", "windowing", "features", "models", "experiments", "training", "split",
                "threshold", "alerts", "evaluation", "outputs"):
        _need(cfg, key, p)
    if p:
        raise ConfigError("Invalid Phase 2 configuration:\n  - " + "\n  - ".join(p))
    w = cfg["windowing"]
    if not (isinstance(w.get("input_window_s"), (int, float)) and w["input_window_s"] > 0):
        p.append("windowing.input_window_s must be a positive number")
    hs = w.get("horizons_s")
    if not (isinstance(hs, list) and hs and all(isinstance(h, (int, float)) and h > 0 for h in hs)):
        p.append("windowing.horizons_s must be a non-empty list of positive numbers")
    elif len(set(hs)) != len(hs):
        p.append("windowing.horizons_s contains duplicates")
    if not (isinstance(w.get("stride_s"), (int, float)) and w["stride_s"] > 0):
        p.append("windowing.stride_s must be a positive number")
    if not 0 < w.get("min_valid_fraction", 1) <= 1:
        p.append("windowing.min_valid_fraction must be in (0, 1]")
    positions = set(profile["sensor"]["channels"]["positions"])
    sets = cfg["data"].get("sensor_sets") or {}
    if not sets:
        p.append("data.sensor_sets must define at least one sensor set")
    for name, pos in sets.items():
        bad = set(pos) - positions
        if bad:
            p.append(f"data.sensor_sets.{name}: unknown sensor positions {sorted(bad)} (dataset has {sorted(positions)})")
    clin = set(profile["clinical"]["fields"])
    for c in cfg["data"].get("clinical_features", []):
        if c not in clin:
            p.append(f"data.clinical_features: {c!r} is not a clinical field in the dataset profile")
        if c == profile["clinical"]["key"]:
            p.append("the participant identifier cannot be a clinical predictor")
    for c in cfg["data"].get("clinical_categorical", []):
        if c not in cfg["data"].get("clinical_features", []):
            p.append(f"data.clinical_categorical: {c!r} is not listed in data.clinical_features")
    ann = set(profile["sensor"]["annotations"])
    leak = {"fog", "fog_severity", "activity", "participant_id", "session_id", "task_id", "timestamp_s"}
    if leak & set(cfg["data"].get("clinical_features", [])):
        p.append(f"data.clinical_features contains identifier/annotation fields: {sorted(leak & set(cfg['data']['clinical_features']))}")
    f = cfg["features"]
    for t in f.get("time_domain", []):
        if t not in TIME_FEATURES:
            p.append(f"features.time_domain: unsupported feature {t!r}; supported: {list(TIME_FEATURES)}")
    fq = f.get("frequency", {})
    for t in fq.get("features", []):
        if t not in FREQ_FEATURES:
            p.append(f"features.frequency.features: unsupported feature {t!r}; supported: {list(FREQ_FEATURES)}")
    if fq.get("enabled") and fq.get("window_function") not in ("hann", "none"):
        p.append("features.frequency.window_function must be hann or none")
    if fq.get("enabled"):
        for a, b in fq.get("ratios", []):
            if a not in fq.get("bands_hz", {}) or b not in fq.get("bands_hz", {}):
                p.append(f"features.frequency.ratios: band {a!r}/{b!r} is not defined in bands_hz")
    for m, spec in cfg["models"].items():
        if spec.get("type") not in MODEL_TYPES:
            p.append(f"models.{m}.type {spec.get('type')!r} unsupported; supported: {list(MODEL_TYPES)}")
    names = [e.get("name") for e in cfg["experiments"]]
    if len(set(names)) != len(names) or not all(names):
        p.append("experiments need unique non-empty names")
    for e in cfg["experiments"]:
        if e.get("sensor_set") not in sets:
            p.append(f"experiments.{e.get('name')}: sensor_set {e.get('sensor_set')!r} is not defined in data.sensor_sets")
        if e.get("clinical") and not cfg["data"].get("clinical_features"):
            p.append(f"experiments.{e.get('name')}: clinical fusion requested but data.clinical_features is empty")
    if cfg["split"].get("strategy") not in SPLIT_STRATEGIES:
        p.append(f"split.strategy must be one of {list(SPLIT_STRATEGIES)}")
    if cfg["threshold"].get("policy") not in THRESHOLD_POLICIES:
        p.append(f"threshold.policy must be one of {list(THRESHOLD_POLICIES)}")
    if cfg["alerts"].get("extra_alerts_for_matched_event") not in ("ignore", "false"):
        p.append("alerts.extra_alerts_for_matched_event must be ignore or false")
    if cfg["alerts"].get("persistence_anchors", 1) < 1:
        p.append("alerts.persistence_anchors must be >= 1")
    if p:
        raise ConfigError("Invalid Phase 2 configuration:\n  - " + "\n  - ".join(p))


def load_phase2(config_path: str | Path = "configs/phase2.yaml", project_root: str | Path | None = None,
                overrides: list[str] | None = None) -> Phase2Settings:
    config_path = Path(config_path).resolve()
    root = Path(project_root).resolve() if project_root else config_path.parent.parent
    raw = load_yaml(config_path)
    if "phase2" not in raw:
        raise ConfigError(f"{config_path} has no `phase2:` section.")
    ov = [o if o.lstrip().startswith("phase2.") else "phase2." + o.lstrip() for o in (overrides or [])]
    cfg = apply_overrides({"phase2": raw["phase2"]}, ov)["phase2"]
    ds_cfg = root / cfg["datasets_config"] if not Path(cfg["datasets_config"]).is_absolute() else Path(cfg["datasets_config"])
    pr_cfg = root / cfg["project_config"] if not Path(cfg["project_config"]).is_absolute() else Path(cfg["project_config"])
    base = load_settings(ds_cfg, pr_cfg, dataset=cfg.get("dataset"), project_root=root)
    validate_phase2(cfg, base.dataset)
    return Phase2Settings(cfg=cfg, base=base, root=root, config_path=config_path)
