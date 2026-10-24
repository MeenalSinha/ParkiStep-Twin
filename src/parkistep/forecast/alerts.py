"""Alert post-processing and event-level scoring.

An alert raised at time `a` is a claim that a freezing episode will START within (a, a + H].
It is a valid early warning for event onset E iff  min_lead <= E - a <= H  (strictly before onset).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def raise_alerts(t: np.ndarray, p: np.ndarray, threshold: float, stride_s: float, alerts_cfg: dict) -> list[float]:
    """Alert times for ONE recording from time-ordered anchor times and probabilities (causal: the alert time is the
    time of the anchor that completes the persistence requirement)."""
    if len(t) == 0:
        return []
    k = int(alerts_cfg.get("persistence_anchors", 1))
    merge_gap = float(alerts_cfg.get("merge_gap_s", 0.0))
    cooldown = float(alerts_cfg.get("cooldown_s", 0.0))
    above = p >= threshold
    if not above.any():
        return []
    cont = np.r_[False, np.diff(t) <= 1.5 * stride_s + 1e-9]          # anchor i continues the series of anchor i-1
    runs: list[tuple[int, int]] = []
    i, n = 0, len(t)
    while i < n:
        if above[i]:
            j = i
            while j + 1 < n and above[j + 1] and cont[j + 1]:
                j += 1
            runs.append((i, j)); i = j + 1
        else:
            i += 1
    runs = [(a, b) for a, b in runs if b - a + 1 >= k]
    groups: list[tuple[int, int]] = []
    for a, b in runs:
        if groups and t[a] - t[groups[-1][1]] <= merge_gap:
            groups[-1] = (groups[-1][0], b)
        else:
            groups.append((a, b))
    times: list[float] = []
    for a, _ in groups:
        at = float(t[a + k - 1])
        if times and at < times[-1] + cooldown:
            continue
        times.append(at)
    return times


def match_alerts(alert_times: list[float], onsets: np.ndarray, event_ids: np.ndarray, evaluable: set, H: float,
                 min_lead_s: float, extra_policy: str) -> dict:
    """Match the alerts of one recording to its annotated onsets (earliest valid alert per event)."""
    detected: dict[str, float] = {}
    n_false = n_redundant = n_true = 0
    for a in sorted(alert_times):
        lead = onsets - a
        valid = np.where((lead >= max(min_lead_s, 1e-9)) & (lead <= H + 1e-9))[0]
        if len(valid) == 0:
            n_false += 1
            continue
        eid = event_ids[valid[np.argmin(lead[valid])]]
        if eid in detected:
            n_redundant += 1
        else:
            detected[eid] = float(onsets[valid[np.argmin(lead[valid])]] - a); n_true += 1
    if extra_policy == "false":
        n_false += n_redundant
    return {"detected": {k: v for k, v in detected.items()}, "n_false": n_false, "n_redundant": n_redundant, "n_true": n_true}


def score_predictions(pred: pd.DataFrame, events: pd.DataFrame, H: float, stride_s: float, alerts_cfg: dict,
                      threshold: float | None = None, per_row_threshold: str | None = "threshold") -> pd.DataFrame:
    """Per-participant event-level counts. `pred` needs recording_id, participant_id, anchor_t, prob, y, event_id
    (target event of positive anchors) and, unless `threshold` is given, a per-row threshold column."""
    rows = []
    ev_by_rec = {r: g for r, g in events.groupby("recording_id")} if len(events) else {}
    for pid, gp in pred.groupby("participant_id", sort=True):
        n_eval = n_det = n_false = n_red = 0
        leads: list[float] = []
        for rid, gr in gp.groupby("recording_id", sort=False):
            gr = gr.sort_values("anchor_t")
            thr = threshold if threshold is not None else float(gr[per_row_threshold].iloc[0])
            at = raise_alerts(gr["anchor_t"].to_numpy(), gr["prob"].to_numpy(), thr, stride_s, alerts_cfg)
            ev = ev_by_rec.get(rid)
            onsets = ev["onset_t"].to_numpy() if ev is not None else np.array([])
            eids = ev["event_id"].to_numpy() if ev is not None else np.array([])
            evaluable = set(gr.loc[gr["y"] == 1, "event_id"])
            m = match_alerts(at, onsets, eids, evaluable, H, float(alerts_cfg.get("min_lead_s", 0.0)),
                             alerts_cfg.get("extra_alerts_for_matched_event", "ignore"))
            n_eval += len(evaluable)
            hit = {e: l for e, l in m["detected"].items() if e in evaluable}
            n_det += len(hit); leads += list(hit.values())
            n_false += m["n_false"]; n_red += m["n_redundant"]
        rows.append({"participant_id": pid, "n_events_evaluable": n_eval, "n_events_detected": n_det,
                     "n_false_alerts": n_false, "n_redundant_alerts": n_red, "monitored_s": float(len(gp) * stride_s),
                     "leads": leads})
    return pd.DataFrame(rows)


def aggregate_event_metrics(per_participant: pd.DataFrame) -> dict:
    ev = int(per_participant["n_events_evaluable"].sum()); det = int(per_participant["n_events_detected"].sum())
    fa = int(per_participant["n_false_alerts"].sum()); mon = float(per_participant["monitored_s"].sum())
    leads = [x for l in per_participant["leads"] for x in l]
    out = {"n_events_evaluable": ev, "n_events_detected": det,
           "event_sensitivity": (det / ev) if ev else None,
           "n_false_alerts": fa, "n_redundant_alerts": int(per_participant["n_redundant_alerts"].sum()),
           "monitored_hours": mon / 3600.0, "false_alerts_per_hour": (fa / (mon / 3600.0)) if mon > 0 else None,
           "n_leads": len(leads)}
    if leads:
        q = np.quantile(leads, [0.1, 0.25, 0.5, 0.75, 0.9])
        out["lead_time_s"] = {"mean": float(np.mean(leads)), "min": float(np.min(leads)), "p10": float(q[0]), "p25": float(q[1]),
                              "median": float(q[2]), "p75": float(q[3]), "p90": float(q[4]), "max": float(np.max(leads))}
    else:
        out["lead_time_s"] = None
    return out


def select_threshold(pred: pd.DataFrame, events: pd.DataFrame, H: float, stride_s: float, alerts_cfg: dict, tcfg: dict) -> dict:
    """Pick an operating threshold from out-of-fold predictions of TRAINING participants only."""
    pol = tcfg["policy"]
    if pol == "fixed":
        return {"policy": pol, "threshold": float(tcfg["fixed_value"])}
    p = pred["prob"].to_numpy()
    if len(np.unique(p)) < 2:
        return {"policy": pol, "threshold": float(np.max(p)) + 1e-9, "note": "constant probabilities; alerts disabled"}
    grid = np.r_[np.linspace(0.0, 1.0, int(tcfg["grid_size"]) + 2)[1:-1], 0.98, 0.99, 0.995, 0.999]   # upper tail matters for rare events
    qs = np.unique(np.quantile(p, grid))
    cand = []
    for thr in qs:
        if pol == "fa_budget":
            m = aggregate_event_metrics(score_predictions(pred, events, H, stride_s, alerts_cfg, threshold=float(thr)))
            cand.append({"threshold": float(thr), "sens": m["event_sensitivity"] or 0.0, "fa": m["false_alerts_per_hour"] or 0.0})
        else:
            yhat = p >= thr; y = pred["y"].to_numpy()
            tp = int((yhat & (y == 1)).sum()); fp = int((yhat & (y == 0)).sum()); fn = int((~yhat & (y == 1)).sum())
            b2 = float(tcfg.get("beta", 1.0)) ** 2
            f = ((1 + b2) * tp) / max(1e-12, (1 + b2) * tp + b2 * fn + fp)
            cand.append({"threshold": float(thr), "score": f})
    if pol == "fa_budget":
        budget = float(tcfg["target_false_alerts_per_hour"])
        ok = [c for c in cand if c["fa"] <= budget]
        if ok:
            best = max(ok, key=lambda c: (c["sens"], -c["fa"], -c["threshold"]))
            return {"policy": pol, "threshold": best["threshold"], "inner_event_sensitivity": best["sens"],
                    "inner_false_alerts_per_hour": best["fa"], "budget": budget}
        top = max(cand, key=lambda c: c["threshold"])
        return {"policy": pol, "threshold": top["threshold"], "note": "no candidate met the false-alert budget; highest threshold used",
                "budget": budget}
    best = max(cand, key=lambda c: c["score"])
    return {"policy": pol, "threshold": best["threshold"], "inner_score": best["score"]}
