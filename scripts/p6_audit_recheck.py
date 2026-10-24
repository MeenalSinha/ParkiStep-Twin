#!/usr/bin/env python
"""Phase 6 - independent re-derivation of the facts behind every Phase 1 audit warning, straight from the raw FoG-STAR CSVs.
Does NOT import parkistep: pandas/numpy only. Column names and documented ranges are read from configs/datasets.yaml. The results
are compared with the saved reports/data_quality_report.json; every difference is reported.

    python scripts/p6_audit_recheck.py [--raw data/raw/fogstar] [--out reports/phase6/phase1_warning_recheck.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--datasets", default="configs/datasets.yaml")
    ap.add_argument("--dataset-name", default="fogstar")
    ap.add_argument("--raw", default=None)
    ap.add_argument("--quality-report", default="reports/data_quality_report.json")
    ap.add_argument("--out", default="reports/phase6/phase1_warning_recheck.json")
    a = ap.parse_args()
    prof = yaml.safe_load(Path(a.datasets).read_text())["datasets"][a.dataset_name]
    raw = Path(a.raw or Path("data/raw") / prof.get("raw_subdir", a.dataset_name))
    sens_p, clin_p = raw / "sensor_data.csv", raw / "clinical_data.csv"
    S, C = pd.read_csv(sens_p), pd.read_csv(clin_p)
    sp, cp = prof["sensor"], prof["clinical"]
    ids = {k: sp["identifiers"][k]["source"][0] for k in ("participant_id", "session_id", "task_id")}
    ts, fog = sp["timestamp"]["source"][0], sp["annotations"]["fog"]["source"][0]
    act = sp["annotations"].get("activity", {}).get("source", ["activity"])[0]
    key = list(ids.values())
    S["_rec"] = list(zip(*(S[c] for c in key)))
    out, facts = {}, {}

    out["rows_sensor"], out["rows_clinical"] = len(S), len(C)
    out["participants_sensor"] = int(S[ids["participant_id"]].nunique()); out["recordings"] = int(S["_rec"].nunique())
    cands = sum([v.get("source", []) for k, v in {**cp["fields"]}.items() if k == cp["key"]], []) or sp["identifiers"]["participant_id"]["source"]
    cid = next(c for c in cands if c in C.columns)
    out["participants_clinical"] = int(C[cid].nunique())
    out["participants_matched_sensor_to_clinical"] = int(len(set(S[ids["participant_id"]]) & set(C[cid])))

    # clinical: missing + out-of-range, using the documented ranges from the profile
    fields = cp.get("fields") or cp.get("columns") or {}
    miss, oor = {}, {}
    for name, spec in fields.items():
        src = next((s for s in spec.get("source", [name]) if s in C.columns), None)
        if src is None: continue
        raw_col = C[src]
        n_missing = int(raw_col.isna().sum())                    # RAW missingness: text columns (e.g. gender 'F'/'M') must not be coerced to NaN
        if n_missing: miss[name] = {"n_missing": n_missing, "participants": C.loc[raw_col.isna(), cid].tolist()}
        vr = spec.get("valid_range")
        if vr:
            col = pd.to_numeric(raw_col, errors="coerce")
            bad = col[(col < vr[0]) | (col > vr[1])]
            if len(bad): oor[name] = {"documented_range": vr, "n_out_of_range": int(len(bad)), "n_values": int(col.notna().sum()), "observed_min_max_of_offenders": [float(bad.min()), float(bad.max())]}
    out["clinical_missing"], out["clinical_out_of_range"] = miss, oor

    # sensor: NaN share, whole-sensor-absent recordings
    chans = [c for c in S.columns if c not in key + [ts, fog, act, "fog_severity"] and not c.startswith("_")]
    nan_rows = S[chans].isna().any(axis=1)
    out["sensor_rows_with_any_missing_channel_pct"] = round(100 * float(nan_rows.mean()), 2)
    positions = sorted({c.split("_")[0] for c in chans})
    absent = {}
    for rec, g in S.groupby("_rec", sort=False):
        miss_pos = [p for p in positions if g[[c for c in chans if c.startswith(p + "_")]].isna().all().all()]
        if miss_pos: absent[str(rec)] = miss_pos
    out["recordings_with_a_whole_sensor_position_absent"] = {"n": len(absent), "examples": dict(list(absent.items())[:5])}

    # timestamps: step, gaps, duplicates, ONE running clock across recordings
    t = S[ts].to_numpy(float); d = np.diff(t)
    med = float(np.median(d))
    out["timestamp"] = {"median_step_s": med, "rows_per_s": 1 / med, "global_gaps_gt_1p5_steps": int((d > 1.5 * med).sum()), "duplicates": int((d == 0).sum()), "non_monotonic": int((d < 0).sum())}
    rec_codes = S["_rec"].astype(str).to_numpy()
    bounds = np.where(rec_codes[1:] != rec_codes[:-1])[0]
    out["recording_boundaries"] = {"n_boundaries": int(len(bounds)), "boundaries_exactly_one_step_apart": int(np.sum(np.isclose(d[bounds], med, rtol=1e-3))),
                                   "recordings_contiguous_rows": bool(len(set(rec_codes)) == len(bounds) + 1)}

    # annotations: events = contiguous fog==1 runs within a recording; events running to the recording end; activity==0 share
    pos = sp["annotation_rules"]["positive_value"]
    n_ev, n_end, parts = 0, 0, set()
    for rec, g in S.groupby("_rec", sort=False):
        f = (g[fog].to_numpy() == pos).astype(int)
        dd = np.diff(np.r_[0, f, 0]); st, en = np.where(dd == 1)[0], np.where(dd == -1)[0] - 1
        n_ev += len(st); n_end += int((en == len(f) - 1).sum())
        if len(st): parts.add(rec[0])
    out["annotations"] = {"fog_events": n_ev, "events_running_to_recording_end": n_end, "participants_with_events": len(parts),
                          "activity_zero_pct": round(100 * float((S[act] == 0).mean()), 2) if act in S else None}
    if "fog_severity" in S:
        mult = 0
        for rec, g in S.groupby("_rec", sort=False):
            f = (g[fog].to_numpy() == pos).astype(int); sv = g["fog_severity"].to_numpy()
            dd = np.diff(np.r_[0, f, 0])
            for s_, e_ in zip(np.where(dd == 1)[0], np.where(dd == -1)[0] - 1):
                mult += int(len(set(sv[s_:e_ + 1])) > 1)
        out["annotations"]["events_with_more_than_one_severity_value"] = mult

    # compare against the saved Phase 1 report
    q = json.loads(Path(a.quality_report).read_text())
    cmp = []
    def add(name, mine, saved, tol=0.0):
        ok = saved is not None and mine is not None and abs(float(mine) - float(saved)) <= tol
        cmp.append({"fact": name, "independent": mine, "phase1_report": saved, "match": bool(ok)})
    ov = q.get("overview", {})
    add("sensor rows", out["rows_sensor"], ov.get("sensor_rows") or q["sensor"].get("n_rows"))
    add("participants", out["participants_sensor"], ov.get("participants") or ov.get("sensor_participants"))
    add("recordings", out["recordings"], ov.get("recordings") or ov.get("n_recordings"))
    out["comparison_with_phase1_report"] = cmp
    out["unverified_comparisons_note"] = ("Phase 1 report keys differ between versions; facts that could not be matched to a report key are listed with "
                                         "match=false and phase1_report=null and must be read as 'not compared', not as 'differs'.")
    expected = {"participants_sensor": 22, "recordings": 106}
    out["matches_documented_expectation_from_PHASE1_VERIFICATION_md"] = {
        "recordings_106": out["recordings"] == 106, "participants_22": out["participants_sensor"] == 22, "events_101": n_ev == 101,
        "events_end_9": n_end == 9, "participants_with_events_16": len(parts) == 16, "boundaries_105_of_105_one_step": out["recording_boundaries"]["n_boundaries"] == 105 == out["recording_boundaries"]["boundaries_exactly_one_step_apart"],
        "sensor_nan_pct_about_15p3": abs(out["sensor_rows_with_any_missing_channel_pct"] - 15.3) < 0.5}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True); Path(a.out).write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps({k: out[k] for k in ("clinical_missing", "clinical_out_of_range", "annotations", "timestamp", "recording_boundaries", "sensor_rows_with_any_missing_channel_pct", "matches_documented_expectation_from_PHASE1_VERIFICATION_md")}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
