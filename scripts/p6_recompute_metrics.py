#!/usr/bin/env python
"""Phase 6 - independent recomputation of the retrospective replay metrics.

Deliberately does NOT import parkistep.forecast.events / alerts / twin.replay. It uses only:
  * the raw FoG-STAR sensor CSV (annotation columns named by configs/datasets.yaml),
  * the saved replay outputs (predictions CSV, warnings JSON, summary JSON),
  * the model manifest (horizon, input window, stride, alert policy).
Event definition, eligibility rules and matching are re-implemented here from the documented rules and then compared
with the saved `retrospective_comparison` in the summary. Every difference is printed.

    python scripts/p6_recompute_metrics.py --tag p2_p2-s1-t4 [--out reports/phase6/retrospective_recompute.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml


def raw_events(csv: Path, prof: dict, rec_key: tuple[int, int, int]):
    """(events, first_t, last_t) for one recording straight from the raw CSV, independent of Phase 1/2 code."""
    sens = prof["sensor"]
    ids = sens["identifiers"]
    id_cols = [ids[k]["source"][0] for k in ("participant_id", "session_id", "task_id")]
    ts_col = sens["timestamp"]["source"][0]
    fog_col = sens["annotations"]["fog"]["source"][0]
    pos = sens["annotation_rules"]["positive_value"]
    parts = []
    for ch in pd.read_csv(csv, usecols=id_cols + [ts_col, fog_col], chunksize=100_000):
        m = (ch[id_cols[0]] == rec_key[0]) & (ch[id_cols[1]] == rec_key[1]) & (ch[id_cols[2]] == rec_key[2])
        if m.any():
            parts.append(ch[m])
    if not parts:
        raise SystemExit(f"recording {rec_key} not found in {csv}")
    g = pd.concat(parts).reset_index(drop=True)
    t = g[ts_col].to_numpy(float)
    f = (g[fog_col].to_numpy() == pos).astype(int)
    d = np.diff(np.r_[0, f, 0])
    starts, ends = np.where(d == 1)[0], np.where(d == -1)[0] - 1
    ev = [{"event": f"e{k}", "onset_t": float(t[s]), "end_t": float(t[e]), "touches_end": bool(e == len(t) - 1)}
          for k, (s, e) in enumerate(zip(starts, ends))]
    return ev, float(t[0]), float(t[-1]), len(g)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True, help="replay output tag, e.g. p2_p2-s1-t4 (files reports/phase3/replay_<tag>_*)")
    ap.add_argument("--replay-dir", default="reports/phase3")
    ap.add_argument("--datasets", default="configs/datasets.yaml")
    ap.add_argument("--dataset-name", default="fogstar")
    ap.add_argument("--raw-csv", default=None, help="default: data/raw/<dataset>/<sensor file pattern>")
    ap.add_argument("--artifacts-root", default="artifacts/phase2")
    ap.add_argument("--out", default="reports/phase6/retrospective_recompute.json")
    a = ap.parse_args()

    rd = Path(a.replay_dir)
    summ = json.loads((rd / f"replay_{a.tag}_summary.json").read_text())
    warns = json.loads((rd / f"replay_{a.tag}_warnings.json").read_text())
    preds = pd.read_csv(rd / f"replay_{a.tag}_predictions.csv")
    saved = summ.get("retrospective_comparison")
    if saved is None:
        raise SystemExit("summary has no retrospective_comparison to compare against")

    prof = yaml.safe_load(Path(a.datasets).read_text())["datasets"][a.dataset_name]
    raw = Path(a.raw_csv) if a.raw_csv else Path("data/raw") / a.dataset_name / prof["files"]["sensor"]["pattern"]

    # --- identity checks: predictions, warnings, summary all refer to the same patient + recording ---------------------
    pid = set(preds["patient_id"].astype(str)), {str(w["patient_id"]) for w in warns}, {str(summ["patient_id"])}
    rid = set(preds["session_id"].astype(str)), {w["session_id"] for w in warns}, {saved["recordings"][0]["recording"]}
    same_patient = len(set.union(*pid)) == 1
    same_recording = len(set.union(*rid)) == 1
    patient, recording = next(iter(pid[0])), next(iter(rid[0]))
    # recording id "p<participant>-s<session>-t<task>" is parsed from the id itself; cross-checked against the raw CSV below
    try:
        key = tuple(int(x[1:]) for x in recording.split("-"))
        assert len(key) == 3 and str(key[0]) == patient
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"cannot interpret recording id {recording!r} for patient {patient}: {e}")

    # --- model facts from the manifest (horizon, window, stride, alert policy) ----------------------------------------
    mid = preds["model_id"].iloc[0]                      # <experiment_id>/<experiment>/<model>
    exp_id, experiment, model = mid.split("/")
    H = float(preds["horizon_s"].iloc[0])
    hk = f"h{int(H) if H == int(H) else H}"
    man = json.loads((Path(a.artifacts_root) / exp_id / "models" / f"{experiment}__{model}__{hk}.manifest.json").read_text())
    L, stride = float(man["window"]["input_window_s"]), float(man["window"]["stride_s"])
    pol = man["alerts"]
    min_lead = float(pol.get("min_lead_s", 0.0))
    thr = float(preds["threshold"].iloc[0])
    assert abs(thr - man["threshold"]["threshold"]) < 1e-12, "threshold in predictions differs from manifest"

    events, lo, hi, n_samples = raw_events(raw, prof, key)
    on = np.array([e["onset_t"] for e in events]); en = np.array([e["end_t"] for e in events])

    # --- eligibility of forecasts and warnings ------------------------------------------------------------------------
    pt = preds["prediction_time"].to_numpy(float)

    def overlaps(t):  # an input window [t-L, t] touching an annotated episode [onset, end] is not a valid pre-onset forecast
        return bool(((on <= t) & (en >= t - L)).any()) if len(on) else False

    elig = np.array([(not overlaps(t)) and (t + H <= hi + 1e-9) for t in pt])
    monitored_s = float(elig.sum()) * stride
    hours = monitored_s / 3600

    wt = sorted(float(w["activated_at"]) for w in warns)
    cand, excl_freeze, excl_cens = [], 0, 0
    for t in wt:
        if overlaps(t): excl_freeze += 1
        elif t + H > hi + 1e-9: excl_cens += 1
        else: cand.append(t)

    detected, n_false, n_redundant = {}, 0, 0
    for t in cand:
        lead = on - t
        ok = np.where((lead >= max(min_lead, 1e-9)) & (lead <= H + 1e-9))[0]     # strictly before onset, within horizon
        if not len(ok): n_false += 1; continue
        i = ok[np.argmin(lead[ok])]
        if i in detected: n_redundant += 1
        else: detected[i] = float(on[i] - t)
    if pol.get("extra_alerts_for_matched_event") == "false": n_false += n_redundant

    covered = [bool(((pt >= on[i] - H) & (pt < on[i]) & elig).any()) for i in range(len(on))]
    leads = [detected[i] for i in sorted(detected)]
    late = [t for t in wt if any((on[i] <= t <= en[i]) for i in range(len(on)))]     # alerts after onset: must never be counted as warnings
    mine = {
        "annotated_events": len(on), "events_with_forecast_coverage": int(sum(covered)), "events_detected": len(detected),
        "event_sensitivity_all_events": len(detected) / len(on) if len(on) else None,
        "event_sensitivity_covered_events": (sum(1 for i in detected if covered[i]) / sum(covered)) if sum(covered) else None,
        "valid_early_warnings": len(detected), "false_warnings": n_false, "redundant_warnings": n_redundant,
        "warnings_excluded_overlap_freezing": excl_freeze, "warnings_excluded_censored_future": excl_cens,
        "scored_monitored_hours": hours, "false_warnings_per_hour": n_false / hours if hours > 0 else None,
        "median_lead_s": float(np.median(leads)) if leads else None,
    }

    # --- extra independent check: re-derive warnings from the saved scores with the saved alert policy ------------------
    prob = preds["probability"].to_numpy(float)
    above = prob >= thr
    runs, i = [], 0
    while i < len(pt):
        if above[i]:
            j = i
            while j + 1 < len(pt) and above[j + 1] and pt[j + 1] - pt[j] <= 1.5 * stride + 1e-9: j += 1
            runs.append((i, j)); i = j + 1
        else: i += 1
    groups = []
    for s_, e_ in runs:
        if groups and pt[s_] - pt[groups[-1][1]] <= float(pol.get("merge_gap_s", 0)): groups[-1] = (groups[-1][0], e_)
        else: groups.append((s_, e_))
    rederived = [float(pt[s_]) for s_, _ in groups]

    # --- compare ------------------------------------------------------------------------------------------------------
    st = saved["totals"]
    diffs, rows = [], []
    for k, v in mine.items():
        sv = st.get(k)
        same = (sv is None and v is None) or (sv is not None and v is not None and abs(float(sv) - float(v)) <= 1e-9 * max(1, abs(float(sv))))
        rows.append({"metric": k, "independent": v, "saved": sv, "match": same})
        if not same: diffs.append(k)
    saved_events = {e["event_id"]: e for e in saved["events"]}
    ev_cmp = []
    for i, e in enumerate(events):
        se = next((x for x in saved["events"] if abs(x["onset_t"] - e["onset_t"]) < 1e-6), None)
        ev_cmp.append({"event": e["event"], "onset_t": e["onset_t"], "end_t": e["end_t"], "independent_detected": i in detected,
                       "independent_lead_s": detected.get(i), "saved_detected": se["detected"] if se else None,
                       "saved_lead_s": se["warning_lead_s"] if se else None, "saved_event_id": se["event_id"] if se else None})
    rep = {
        "schema": "parkistep.phase6.retrospective_recompute/1", "tag": a.tag, "patient_id": patient, "recording_id": recording,
        "raw_recording_samples": n_samples, "raw_first_t": lo, "raw_last_t": hi,
        "identity_checks": {"patient_same_in_predictions_warnings_summary": same_patient, "recording_same": same_recording,
                            "prediction_rows": int(len(preds)), "warnings": len(warns)},
        "model": {"id": mid, "horizon_s": H, "input_window_s": L, "stride_s": stride, "threshold": thr, "alert_policy": pol},
        "leakage_checks": {"alerts_activated_at_or_after_an_event_onset": len(late),
                           "note": "such alerts fall inside an annotated episode and are excluded as 'overlap with freezing'; none is credited as warning"},
        "independent": mine, "comparison": rows, "events": ev_cmp,
        "warnings_rederived_from_scores": {"count": len(rederived), "saved_count": len(wt),
                                           "same_times": bool(len(rederived) == len(wt) and np.allclose(rederived, wt, atol=1e-6)),
                                           "note": "re-derivation uses threshold + merge gap only; the engine also applies hysteresis/cooldown/status rules, so a difference is explained, not an error, only if documented"},
        "n_discrepancies": len(diffs), "discrepant_metrics": diffs,
    }
    out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, indent=2, default=float))
    print(json.dumps({k: rep[k] for k in ("patient_id", "recording_id", "identity_checks", "n_discrepancies", "discrepant_metrics")}, indent=1))
    for r in rows: print(f"{'OK ' if r['match'] else 'DIFF'} {r['metric']:42s} independent={r['independent']}  saved={r['saved']}")
    print("warnings re-derived from scores:", rep["warnings_rederived_from_scores"])
    return 1 if diffs or not (same_patient and same_recording) else 0


if __name__ == "__main__":
    sys.exit(main())
