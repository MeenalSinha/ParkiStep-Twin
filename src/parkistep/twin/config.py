"""Phase 3 configuration: loading, validation, hashing. No runtime value lives in code."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from ..config import ConfigError, apply_overrides, load_yaml
from ..forecast.settings import Phase2Settings, load_phase2

SCHEMA_VERSION = "parkistep.twin.state/1"          # version of the externally visible state schema
TWIN_STATUSES = ("INITIALIZING", "WAITING_FOR_DATA", "WARMING_UP", "READY", "DEGRADED_DATA_QUALITY", "STALE",
                 "MODEL_UNAVAILABLE", "ERROR")
# sections that change how a twin evolves; a persisted twin is only restored under the same values (or an explicit override)
STATE_AFFECTING_SECTIONS = ("patient", "observation", "ingestion", "buffer", "model", "cadence", "forecast", "warning",
                            "baseline", "lifecycle")


@dataclass
class Phase3Settings:
    cfg: dict
    phase2: Phase2Settings
    root: Path
    config_path: Path

    def __getitem__(self, k):
        return self.cfg[k]

    def resolve(self, p) -> Path:
        p = Path(p)
        return p if p.is_absolute() else self.root / p

    @property
    def profile(self) -> dict:
        return self.phase2.base.dataset

    @property
    def config_hash(self) -> str:
        return hashlib.sha256(json.dumps(self.cfg, sort_keys=True, default=str).encode()).hexdigest()

    @property
    def compat_hash(self) -> str:
        sub = {k: self.cfg[k] for k in STATE_AFFECTING_SECTIONS}
        return hashlib.sha256(json.dumps(sub, sort_keys=True, default=str).encode()).hexdigest()

    @property
    def model_dir(self) -> Path:
        d = self.cfg["model"].get("artifact_dir")
        return self.resolve(d) if d else self.phase2.artifact_dir / "models"

    @property
    def state_path(self) -> Path:
        return self.resolve(self.cfg["persistence"]["path"])

    @property
    def output_dir(self) -> Path:
        return self.resolve(self.cfg["outputs"]["dir"])

    @property
    def patient_id_field(self) -> str:
        return self.cfg["patient"]["id_field"]

    def snapshot(self) -> dict:
        return {"phase3": self.cfg, "config_hash": self.config_hash, "compat_hash": self.compat_hash,
                "config_file": str(self.config_path), "phase2_experiment_id": self.phase2.experiment_id}


def _need(cfg: dict, dotted: str, problems: list[str]):
    cur = cfg
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            problems.append(f"missing required setting `phase3.{dotted}`")
            return None
        cur = cur[part]
    return cur


def _num(cfg, dotted, problems, *, positive=False, nonneg=False, allow_none=False):
    cur = cfg
    for part in dotted.split("."):
        cur = cur.get(part) if isinstance(cur, dict) else None
    if cur is None:
        if not allow_none:
            problems.append(f"phase3.{dotted} is required")
        return None
    if isinstance(cur, bool) or not isinstance(cur, (int, float)):
        problems.append(f"phase3.{dotted} must be a number, got {cur!r}")
        return None
    if positive and cur <= 0:
        problems.append(f"phase3.{dotted} must be > 0, got {cur}")
    if nonneg and cur < 0:
        problems.append(f"phase3.{dotted} must be >= 0, got {cur}")
    return cur


def _choice(cfg, dotted, options, problems):
    cur = cfg
    for part in dotted.split("."):
        cur = cur.get(part) if isinstance(cur, dict) else None
    if cur not in options:
        problems.append(f"phase3.{dotted} must be one of {list(options)}, got {cur!r}")


def validate_phase3(cfg: dict, phase2: Phase2Settings) -> None:
    p: list[str] = []
    for key in ("patient", "observation", "ingestion", "buffer", "model", "cadence", "forecast", "warning", "baseline",
                "lifecycle", "persistence", "retention", "logging", "replay", "outputs"):
        _need(cfg, key, p)
    if p:
        raise ConfigError("Invalid Phase 3 configuration:\n  - " + "\n  - ".join(p))
    prof = phase2.base.dataset
    pa = cfg["patient"]
    try:
        re.compile(pa.get("id_pattern", ""))
    except re.error as e:
        p.append(f"patient.id_pattern is not a valid regular expression: {e}")
    if "{patient_id}" not in str(pa.get("twin_id_template", "")):
        p.append("patient.twin_id_template must contain {patient_id}")
    if pa.get("id_field") not in prof["clinical"]["fields"] and pa.get("id_field") != prof["clinical"]["key"]:
        p.append(f"patient.id_field {pa.get('id_field')!r} is not a field of the dataset profile")
    _choice(cfg, "patient.exists_policy", ("error", "return_existing"), p)
    _choice(cfg, "patient.clinical_source", ("phase1_processed", "none"), p)
    _choice(cfg, "patient.clinical_missing_policy", ("model_imputation", "require_all"), p)
    m = pa.get("clinical_feature_mapping")
    if m is not None:
        if not isinstance(m, dict):
            p.append("patient.clinical_feature_mapping must be null or a mapping")
        else:
            for k, v in m.items():
                if v not in prof["clinical"]["fields"]:
                    p.append(f"patient.clinical_feature_mapping: {k!r} -> {v!r} is not a clinical field of the dataset profile")
    o = cfg["observation"]
    ts = o.get("timestamp", {})
    if ts.get("kind") not in ("numeric", "datetime"):
        p.append("observation.timestamp.kind must be numeric or datetime")
    if ts.get("kind") == "numeric" and ts.get("unit") not in ("seconds", "milliseconds", "microseconds"):
        p.append("observation.timestamp.unit must be seconds, milliseconds or microseconds")
    for f in ("field",):
        if not ts.get(f):
            p.append(f"observation.timestamp.{f} is required")
    if not o.get("patient_field"):
        p.append("observation.patient_field is required")
    if not (isinstance(o.get("session_fields"), list) and o["session_fields"] and all(isinstance(x, str) for x in o["session_fields"])):
        p.append("observation.session_fields must be a non-empty list of field names")
    ch = o.get("channels", {})
    if ch.get("mode") not in ("identity", "mapping"):
        p.append("observation.channels.mode must be identity or mapping")
    if ch.get("mode") == "mapping" and not (isinstance(ch.get("mapping"), dict) and ch["mapping"]):
        p.append("observation.channels.mapping must be a non-empty mapping when mode = mapping")
    if o.get("dtype") != "float":
        p.append("observation.dtype: only 'float' is supported")
    g = cfg["ingestion"]
    _choice(cfg, "ingestion.duplicate_policy", ("reject", "ignore_identical"), p)
    _choice(cfg, "ingestion.out_of_order.policy", ("reject", "reorder_within_tolerance"), p)
    _choice(cfg, "ingestion.gap_policy", ("reset_buffer", "flag_only"), p)
    _choice(cfg, "ingestion.missing_channel_policy", ("mark_missing", "reject"), p)
    _choice(cfg, "ingestion.nonfinite_policy", ("mark_missing", "reject"), p)
    _choice(cfg, "ingestion.session_change_policy", ("reset_buffer", "require_explicit_end"), p)
    _num(cfg, "ingestion.max_gap_s", p, positive=True, allow_none=True)
    tol = _num(cfg, "ingestion.out_of_order.reorder_tolerance_s", p, nonneg=True)
    mh = _num(cfg, "ingestion.out_of_order.max_held", p, positive=True)
    if g["out_of_order"].get("policy") == "reorder_within_tolerance" and tol is not None and tol <= 0:
        p.append("ingestion.out_of_order.reorder_tolerance_s must be > 0 for the reorder policy")
    b = cfg["buffer"]
    _num(cfg, "buffer.max_duration_s", p, positive=True)
    _num(cfg, "buffer.max_observations", p, positive=True)
    _num(cfg, "buffer.warmup_extra_s", p, nonneg=True)
    mvf = _num(cfg, "buffer.min_valid_fraction", p, positive=True, allow_none=True)
    if mvf is not None and mvf > 1:
        p.append("buffer.min_valid_fraction must be in (0, 1]")
    mo = cfg["model"]
    for k in ("experiment", "model"):
        if not isinstance(mo.get(k), str) or not mo[k]:
            p.append(f"model.{k} must be a non-empty string")
    _num(cfg, "model.horizon_s", p, positive=True)
    _choice(cfg, "model.on_unavailable", ("fail", "degrade"), p)
    _choice(cfg, "model.expected_dt_source", ("manifest", "config"), p)
    if mo.get("expected_dt_source") == "config":
        _num(cfg, "model.expected_dt_s", p, positive=True)
    _num(cfg, "cadence.every_s", p, positive=True, allow_none=True)
    _num(cfg, "forecast.stale_after_cadences", p, positive=True)
    _choice(cfg, "forecast.on_input_invalid", ("no_forecast",), p)
    w = cfg["warning"]
    _choice(cfg, "warning.policy_source", ("model_manifest", "config"), p)
    k = _num(cfg, "warning.persistence_anchors", p, positive=True)
    if k is not None and int(k) != k:
        p.append("warning.persistence_anchors must be an integer")
    _num(cfg, "warning.merge_gap_s", p, nonneg=True)
    _num(cfg, "warning.cooldown_s", p, nonneg=True)
    cb = _num(cfg, "warning.clear.consecutive_below", p, positive=True)
    if cb is not None and int(cb) != cb:
        p.append("warning.clear.consecutive_below must be an integer")
    if not isinstance(w.get("allow_repeat_while_active"), bool):
        p.append("warning.allow_repeat_while_active must be true or false")
    _num(cfg, "warning.expire_after_invalid_s", p, positive=True, allow_none=True)
    _num(cfg, "warning.max_active_s", p, positive=True, allow_none=True)
    _choice(cfg, "warning.threshold.source", ("model_manifest",), p)
    fv = w["threshold"].get("fallback_value")
    if fv is not None and not (isinstance(fv, (int, float)) and not isinstance(fv, bool) and 0 <= fv <= 1):
        p.append("warning.threshold.fallback_value must be null or a number in [0, 1]")
    bad = [s for s in w.get("suppress_when_status", []) if s not in TWIN_STATUSES]
    if bad:
        p.append(f"warning.suppress_when_status contains unknown statuses {bad}; valid: {list(TWIN_STATUSES)}")
    bl = cfg["baseline"]
    _choice(cfg, "baseline.statistic", ("std", "rms"), p)
    sig = set(prof["sensor"]["channels"]["signals"])
    if not isinstance(bl.get("signals"), list) or not bl["signals"] or set(bl["signals"]) - sig:
        p.append(f"baseline.signals must be a non-empty list drawn from {sorted(sig)}")
    a = _num(cfg, "baseline.ewma_alpha", p, positive=True)
    if a is not None and a > 1:
        p.append("baseline.ewma_alpha must be in (0, 1]")
    _num(cfg, "baseline.min_updates", p, positive=True)
    _num(cfg, "baseline.skip_if_abs_z_above", p, positive=True, allow_none=True)
    bad = [s for s in bl.get("update_only_when_status", []) if s not in TWIN_STATUSES]
    if bad:
        p.append(f"baseline.update_only_when_status contains unknown statuses {bad}")
    _num(cfg, "lifecycle.stale_after_s", p, positive=True, allow_none=True)
    _choice(cfg, "lifecycle.error_recovery", ("auto", "manual"), p)
    pe = cfg["persistence"]
    _choice(cfg, "persistence.backend", ("sqlite", "memory"), p)
    _choice(cfg, "persistence.snapshot_policy", ("on_event", "every_update", "manual"), p)
    _choice(cfg, "persistence.on_incompatible", ("error", "allow_config_change"), p)
    if pe.get("enabled") and pe.get("backend") == "sqlite" and not pe.get("path"):
        p.append("persistence.path is required for the sqlite backend")
    for key in ("prediction_history_max", "warning_history_max", "event_history_max", "observation_log_max",
                "prediction_store_max", "event_store_max"):
        _num(cfg, f"retention.{key}", p, positive=True, allow_none=True)
    lvl = str(cfg["logging"].get("level", "")).upper()
    if lvl not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        p.append("logging.level must be DEBUG, INFO, WARNING, ERROR or CRITICAL")
    r = cfg["replay"]
    _choice(cfg, "replay.scope", ("recording", "participant"), p)
    _choice(cfg, "replay.selection_policy", ("most_events", "longest", "first"), p)
    _choice(cfg, "replay.clock", ("observation", "wall"), p)
    _num(cfg, "replay.speed", p, nonneg=True)
    _num(cfg, "replay.max_observations", p, positive=True, allow_none=True)
    if r.get("recording_id") is not None and r.get("scope") == "participant":
        p.append("replay.recording_id cannot be combined with replay.scope = participant")
    if not cfg["outputs"].get("dir"):
        p.append("outputs.dir is required")
    if p:
        raise ConfigError("Invalid Phase 3 configuration:\n  - " + "\n  - ".join(p))


def load_phase3(config_path: str | Path = "configs/phase3.yaml", project_root: str | Path | None = None,
                overrides: list[str] | None = None) -> Phase3Settings:
    config_path = Path(config_path).resolve()
    root = Path(project_root).resolve() if project_root else config_path.parent.parent
    raw = load_yaml(config_path)
    if "phase3" not in raw:
        raise ConfigError(f"{config_path} has no `phase3:` section.")
    ov = [o if o.lstrip().startswith("phase3.") else "phase3." + o.lstrip() for o in (overrides or [])]
    cfg = apply_overrides({"phase3": raw["phase3"]}, ov)["phase3"]
    p2 = cfg.get("phase2_config")
    if not p2:
        raise ConfigError("phase3.phase2_config is required (path to the Phase 2 configuration).")
    p2path = Path(p2) if Path(p2).is_absolute() else root / p2
    phase2 = load_phase2(p2path, root)
    validate_phase3(cfg, phase2)
    return Phase3Settings(cfg=cfg, phase2=phase2, root=root, config_path=config_path)
