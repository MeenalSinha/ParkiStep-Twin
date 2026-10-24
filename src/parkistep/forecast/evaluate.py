"""Phase 2 evaluation: window-level + event-level metrics from saved out-of-fold predictions, participant bootstrap,
sensor-only vs fusion comparison, and the generated results report. Nothing here refits a model."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

from .alerts import aggregate_event_metrics, score_predictions
from .experiment import load_cache
from .prepare import Phase2Error
from .settings import Phase2Settings
from .windows import horizon_key

log = logging.getLogger(__name__)


def _safe(fn, *a):
    try:
        return float(fn(*a))
    except ValueError:
        return None


def window_metrics(y: np.ndarray, p: np.ndarray, thr: np.ndarray) -> dict:
    n_pos = int(y.sum())
    out = {"n_windows": int(len(y)), "n_positive": n_pos, "prevalence": float(y.mean()) if len(y) else None,
           "average_precision": _safe(average_precision_score, y, p) if n_pos else None,
           "roc_auc": _safe(roc_auc_score, y, p) if 0 < n_pos < len(y) else None,
           "brier": _safe(brier_score_loss, y, p)}
    if len(y):
        out["brier_prevalence_reference"] = float(np.mean((y.mean() - y) ** 2))
    yhat = p >= thr
    tp = int((yhat & (y == 1)).sum()); fp = int((yhat & (y == 0)).sum()); fn = int((~yhat & (y == 1)).sum())
    prec = tp / (tp + fp) if tp + fp else None
    rec = tp / (tp + fn) if tp + fn else None
    out["at_selected_thresholds"] = {"precision": prec, "recall": rec,
                                     "f1": (2 * prec * rec / (prec + rec)) if prec and rec else (0.0 if prec is not None and rec is not None else None),
                                     "n_alert_windows": int(yhat.sum())}
    return out


def calibration_table(y, p, bins):
    edges = np.linspace(0, 1, bins + 1)
    ix = np.clip(np.digitize(p, edges[1:-1]), 0, bins - 1)
    rows = []
    for b in range(bins):
        m = ix == b
        if m.any():
            rows.append({"bin": b, "mean_predicted": float(p[m].mean()), "observed_rate": float(y[m].mean()), "n": int(m.sum())})
    return rows


def _ci(vals, level):
    v = np.array([x for x in vals if x is not None and np.isfinite(x)], dtype=float)
    if len(v) < 10:
        return None
    a = (1 - level) / 2
    return [float(np.quantile(v, a)), float(np.quantile(v, 1 - a))]


def run_evaluate(s: Phase2Settings) -> Path:
    cfg = s.cfg
    art = s.artifact_dir
    oofdir = art / "oof"
    files = sorted(oofdir.glob("*.parquet")) if oofdir.exists() else []
    if not files:
        raise Phase2Error(f"No out-of-fold predictions in {oofdir}; run scripts/train_phase2.py first.")
    c = load_cache(s)
    events = c["events"]; summary = c["summary"]
    stride = float(cfg["windowing"]["stride_s"]); acfg = cfg["alerts"]
    B = int(cfg["evaluation"]["bootstrap"]["n_resamples"]); level = float(cfg["evaluation"]["bootstrap"]["ci"])
    universe = sorted(events["participant_id"].unique().tolist() + [p for p in pd.read_csv(art / "split_manifest.csv")["participant_id"] if p not in set(events["participant_id"])])
    rng = np.random.RandomState(int(cfg["evaluation"]["bootstrap"]["seed"]))
    resamples = [rng.choice(universe, size=len(universe), replace=True) for _ in range(B)]
    results: dict = {}
    for fp in files:
        pred = pd.read_parquet(fp)
        exp, model, H = pred["experiment"].iloc[0], pred["model"].iloc[0], float(pred["horizon_s"].iloc[0])
        key = f"{exp}|{model}|{horizon_key(H)}"
        y = pred["y"].to_numpy(); p = pred["prob"].to_numpy(); thr = pred["threshold"].to_numpy()
        wm = window_metrics(y, p, thr)
        pp = score_predictions(pred, events, H, stride, acfg)
        em = aggregate_event_metrics(pp)
        per_fold = []
        for f, g in pred.groupby("fold"):
            gm = window_metrics(g["y"].to_numpy(), g["prob"].to_numpy(), g["threshold"].to_numpy())
            ge = aggregate_event_metrics(score_predictions(g, events, H, stride, acfg))
            per_fold.append({"fold": int(f), "n_participants": int(g["participant_id"].nunique()), "n_events_evaluable": ge["n_events_evaluable"],
                             "average_precision": gm["average_precision"], "event_sensitivity": ge["event_sensitivity"],
                             "false_alerts_per_hour": ge["false_alerts_per_hour"], "threshold": float(g["threshold"].iloc[0])})
        per_part = [{"participant_id": int(r.participant_id), "n_events_evaluable": int(r.n_events_evaluable), "n_events_detected": int(r.n_events_detected),
                     "n_false_alerts": int(r.n_false_alerts), "monitored_s": float(r.monitored_s)} for r in pp.itertuples()]
        # participant bootstrap (same resamples for every combination -> paired comparisons are valid)
        by_p = {k: g for k, g in pred.groupby("participant_id")}
        pp_i = pp.set_index("participant_id")
        boot = {"average_precision": [], "event_sensitivity": [], "false_alerts_per_hour": [], "brier": [], "median_lead_s": []}
        for rs in resamples:
            parts = [q for q in rs if q in by_p]
            if not parts:
                continue
            yb = np.concatenate([by_p[q]["y"].to_numpy() for q in parts]); pb = np.concatenate([by_p[q]["prob"].to_numpy() for q in parts])
            boot["average_precision"].append(_safe(average_precision_score, yb, pb) if yb.sum() else None)
            boot["brier"].append(_safe(brier_score_loss, yb, pb))
            sub = pd.concat([pp_i.loc[[q]] for q in parts])
            ag = aggregate_event_metrics(sub)
            boot["event_sensitivity"].append(ag["event_sensitivity"]); boot["false_alerts_per_hour"].append(ag["false_alerts_per_hour"])
            boot["median_lead_s"].append(ag["lead_time_s"]["median"] if ag["lead_time_s"] else None)
        results[key] = {"experiment": exp, "model": model, "horizon_s": H, "window": wm, "event": em, "per_fold": per_fold,
                        "per_participant": per_part,
                        "calibration": calibration_table(y, p, int(cfg["evaluation"]["calibration_bins"])),
                        "bootstrap": {k: {"ci": _ci(v, level), "n_valid": int(sum(x is not None for x in v)), "samples": [x for x in v]} for k, v in boot.items()},
                        "thresholds": [float(t) for t in sorted(pred.groupby("fold")["threshold"].first())],
                        "n_participants": int(pred["participant_id"].nunique()),
                        "n_participants_with_evaluable_events": int((pp["n_events_evaluable"] > 0).sum())}
        log.info("evaluated %s", key)
    comparison = _compare(results, cfg, level)
    selection = _select(results, cfg)
    rep = s.report_dir; rep.mkdir(parents=True, exist_ok=True)
    slim = json.loads(json.dumps(results, default=str))
    for v in slim.values():
        for b in v["bootstrap"].values():
            b.pop("samples", None)
    payload = {"experiment_id": s.experiment_id, "config_hash": s.config_hash, "results": slim, "comparison": comparison,
               "selection": selection, "window_summary": summary}
    (rep / "phase2_metrics.json").write_text(json.dumps(payload, indent=2, default=str))
    (art / "phase2_metrics.json").write_text(json.dumps(payload, indent=2, default=str))
    from .report import write_report
    write_report(s, payload, rep / "phase2_results_report.md")
    log.info("Reports written to %s", rep)
    return rep


def _compare(results, cfg, level):
    out = []
    exps = [e["name"] for e in cfg["experiments"]]
    if "sensor_only" not in exps or "fusion" not in exps:
        return out
    for mname in cfg["models"]:
        for H in cfg["windowing"]["horizons_s"]:
            hk = horizon_key(float(H))
            a, b = results.get(f"sensor_only|{mname}|{hk}"), results.get(f"fusion|{mname}|{hk}")
            if not a or not b:
                continue
            row = {"model": mname, "horizon_s": float(H)}
            for m, getter in (("average_precision", lambda r: r["window"]["average_precision"]),
                              ("brier", lambda r: r["window"]["brier"]),
                              ("event_sensitivity", lambda r: r["event"]["event_sensitivity"]),
                              ("false_alerts_per_hour", lambda r: r["event"]["false_alerts_per_hour"]),
                              ("median_lead_s", lambda r: (r["event"]["lead_time_s"] or {}).get("median"))):
                va, vb = getter(a), getter(b)
                sa, sb = a["bootstrap"][m]["samples"], b["bootstrap"][m]["samples"]
                n = min(len(sa), len(sb))
                d = [(None if (x is None or y is None) else y - x) for x, y in zip(sa[:n], sb[:n])]
                row[m] = {"sensor_only": va, "fusion": vb, "delta": (None if va is None or vb is None else vb - va), "delta_ci": _ci(d, level)}
            out.append(row)
    return out


def _select(results, cfg):
    sel = cfg["evaluation"]["selection"]; out = {}
    for H in cfg["windowing"]["horizons_s"]:
        hk = horizon_key(float(H)); best = None
        for k, r in results.items():
            if r["horizon_s"] != float(H) or r["model"] not in sel["among"]:
                continue
            v = r["window"].get(sel["metric"])
            if v is not None and (best is None or v > best[0]):
                best = (v, r["experiment"], r["model"])
        out[hk] = {"metric": sel["metric"], "value": best[0], "experiment": best[1], "model": best[2]} if best else None
    return out
