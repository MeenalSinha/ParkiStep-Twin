"""Phase 2 step 1: events, anchors, forecasting labels and window features (cached on disk)."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import channel_specs
from .events import extract_events
from .features import (build_spec, causal_fill, position_quality, window_features, windows_from_signal)
from .settings import Phase2Settings
from .windows import R_OK, build_anchor_table, horizon_key, summarize_targets

log = logging.getLogger(__name__)


class Phase2Error(RuntimeError):
    pass


def read_table(base: Path) -> pd.DataFrame:
    for ext, fn in ((".parquet", pd.read_parquet), (".csv", pd.read_csv)):
        p = base.with_suffix(ext)
        if p.exists():
            return fn(p)
    raise Phase2Error(f"Phase 1 output not found: {base}.parquet|.csv. Run `python scripts/prepare_data.py` first.")


def dataset_columns(s: Phase2Settings) -> dict:
    prof = s.base.dataset
    ann = prof["sensor"]["annotations"]
    rules = prof["sensor"]["annotation_rules"]
    ids = prof["sensor"]["identifiers"]
    label_key = rules["event_label"]
    return {"recording": "recording_id", "participant": "participant_id", "session": "session_id", "task": "task_id",
            "time": prof["sensor"]["timestamp"]["canonical"], "label": label_key, "positive": rules["positive_value"],
            "severity": rules.get("severity"), "activity": next((k for k, v in ann.items() if v.get("role") == "activity"), None),
            "clinical_key": prof["clinical"]["key"], "id_keys": list(ids)}


def load_phase1_tables(s: Phase2Settings) -> tuple[pd.DataFrame, pd.DataFrame]:
    stems = s.base.project["outputs"]["file_stems"]
    d = s.base.processed_dir
    man = d / s.base.project["outputs"]["manifest"]
    if not man.exists():
        raise Phase2Error(f"Phase 1 manifest missing at {man}; run the Phase 1 pipeline first.")
    status = json.loads(man.read_text()).get("audit_status")
    if status not in ("PASS", "PASS_WITH_WARNINGS"):
        raise Phase2Error(f"Phase 1 audit status is {status!r}; Phase 2 requires PASS or PASS_WITH_WARNINGS.")
    if json.loads(man.read_text()).get("synthetic_test_data"):
        raise Phase2Error("Phase 1 outputs are marked synthetic; refusing to run Phase 2 on them.")
    return read_table(d / stems["sensor"]), read_table(d / stems["clinical"])


def run_prepare(s: Phase2Settings, sensor: pd.DataFrame | None = None, clinical: pd.DataFrame | None = None,
                force: bool = False, allow_synthetic: bool = False) -> Path:
    cfg = s.cfg
    out = s.cache_dir
    if (out / "window_summary.json").exists() and not force:
        log.info("Window cache exists at %s (use --force to rebuild)", out)
        return out
    if sensor is None:
        sensor, clinical = load_phase1_tables(s)
    cols = dataset_columns(s)
    W = cfg["windowing"]; horizons = [float(h) for h in W["horizons_s"]]
    t0 = time.time()
    events = extract_events(sensor, recording_col=cols["recording"], participant_col=cols["participant"],
                            session_col=cols["session"], task_col=cols["task"], time_col=cols["time"],
                            label_col=cols["label"], positive_value=cols["positive"], severity_col=cols["severity"],
                            merge_gap_s=cfg["events"]["merge_gap_s"], min_duration_s=cfg["events"]["min_duration_s"])
    if events.empty:
        raise Phase2Error("No annotated events found; forecasting is not possible.")
    log.info("Extracted %d events from %d recordings", len(events), sensor[cols["recording"]].nunique())
    anchors = build_anchor_table(sensor, events, cols=cols, windowing=W, horizons=horizons,
                                 eligible_tasks=cfg["data"].get("eligible_tasks"),
                                 eligible_activities=cfg["data"].get("eligible_activities"), activity_col=cols["activity"])
    anchors.insert(0, "window_id", np.arange(len(anchors)))
    spec = build_spec(s.base.dataset, cfg["features"])
    raw_cols = spec.raw_channels
    missing = [c for c in raw_cols if c not in sensor.columns]
    if missing:
        raise Phase2Error(f"Sensor table lacks configured channels: {missing[:5]}")
    pos_qc = {p: np.zeros(len(anchors), bool) for p in spec.positions}
    feats = []
    grp = anchors.groupby("recording_id", sort=False).indices
    for rid, g in sensor.groupby(cols["recording"], sort=False):
        rows = grp.get(rid)
        if rows is None:
            continue
        a = anchors.iloc[rows]
        a = a[a["base_status"] == R_OK]
        if a.empty:
            continue
        n = int(a["n_window_samples"].iloc[0]); dt = float(a["dt_s"].iloc[0])
        raw = g[raw_cols].to_numpy(dtype=np.float64)
        filled = causal_fill(raw, int(W["max_ffill_samples"]))
        end = a["anchor_idx"].to_numpy()
        q = position_quality(raw, filled, end, n, spec, float(W["min_valid_fraction"]))
        for p in spec.positions:
            pos_qc[p][a.index.to_numpy()] = q[p]
        Wn = windows_from_signal(filled, end, n)
        f = window_features(Wn, spec, fs=1.0 / dt)
        f.insert(0, "window_id", a["window_id"].to_numpy())
        feats.append(f)
    features = pd.concat(feats, ignore_index=True)
    for p in spec.positions:
        anchors[f"qc_{p}"] = pos_qc[p]
    out.mkdir(parents=True, exist_ok=True)
    events.to_parquet(out / "events.parquet", index=False)
    anchors.to_parquet(out / "anchors.parquet", index=False)
    features.to_parquet(out / "features.parquet", index=False)
    if clinical is not None:
        clinical.to_parquet(out / "clinical.parquet", index=False)
    summary = summarize_targets(anchors, events, horizons, float(W["stride_s"]))
    summary["feature_spec"] = spec.to_dict()
    summary["n_features"] = len(spec.names)
    summary["n_feature_rows"] = int(len(features))
    summary["quality_counts"] = {p: int(anchors.loc[anchors["base_status"] == R_OK, f"qc_{p}"].sum()) for p in spec.positions}
    summary["n_events_by_task"] = {int(k): int(v) for k, v in events["task_id"].value_counts().items()}
    summary["events_touching_recording_end"] = int(events["touches_recording_end"].sum())
    summary["build_seconds"] = round(time.time() - t0, 1)
    summary["config"] = s.snapshot()
    (out / "window_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    log.info("Wrote windows to %s (%d anchors, %d feature rows, %d features) in %.0fs", out, len(anchors), len(features),
             len(spec.names), summary["build_seconds"])
    return out
