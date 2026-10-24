"""Leakage-safe anchor generation and forecasting-target labels.

An anchor is a sample index `i` of one recording; prediction time t = timestamp[i]. The input window is the last
`n` samples ending at i (inclusive); the prediction interval is (t, t + H]. Nothing here looks across recordings.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

R_OK = "eligible_base"
BASE_REASONS = ["insufficient_history", "timestamp_irregular_window", "input_window_overlaps_event",
                "ongoing_event_at_anchor", "too_soon_after_previous_event", "task_not_eligible", "activity_not_eligible"]


def expected_samples(window_s: float, dt: float) -> int:
    """Number of samples covering `window_s` at sampling interval dt (derived from the data, not assumed)."""
    return int(round(window_s / dt)) + 1


def recording_dt(ts: np.ndarray) -> float:
    d = np.diff(ts)
    d = d[d > 0]
    if len(d) == 0:
        raise ValueError("recording has no positive timestamp intervals")
    return float(np.median(d))


def build_anchor_table(sensor: pd.DataFrame, events: pd.DataFrame, *, cols: dict, windowing: dict,
                       horizons: list[float], eligible_tasks=None, eligible_activities=None,
                       activity_col: str | None = None) -> pd.DataFrame:
    """One row per candidate anchor with base eligibility and, per horizon, label/eligibility/status."""
    L = float(windowing["input_window_s"]); stride = float(windowing["stride_s"])
    tol = float(windowing.get("timestamp_tolerance_fraction", 0.05))
    excl_overlap = bool(windowing.get("exclude_input_overlapping_event", True))
    min_since = float(windowing.get("min_time_since_previous_event_s", 0.0))
    full_future = bool(windowing.get("require_full_future", True))
    ev_by_rec = {k: v for k, v in events.groupby("recording_id")} if len(events) else {}
    out = []
    for rid, g in sensor.groupby(cols["recording"], sort=False):
        ts = g[cols["time"]].to_numpy(dtype=float)
        n_rec = len(ts)
        if n_rec < 2:
            continue
        dt = recording_dt(ts)
        n_win = expected_samples(L, dt)
        step = max(1, int(round(stride / dt)))
        fog = (g[cols["label"]].to_numpy() == cols["positive"])
        first_valid = n_win - 1
        all_idx = np.arange(0, n_rec, step)
        cum = np.r_[0, np.cumsum(fog)]
        base = np.full(len(all_idx), R_OK, dtype=object)
        span_ok = np.zeros(len(all_idx), bool)
        hist = all_idx >= first_valid
        for k, i in enumerate(all_idx):
            if not hist[k]:
                base[k] = "insufficient_history"; continue
            span = ts[i] - ts[i - n_win + 1]
            exp_span = (n_win - 1) * dt
            if abs(span - exp_span) > tol * exp_span:
                base[k] = "timestamp_irregular_window"; continue
            if fog[i]:
                base[k] = "ongoing_event_at_anchor"; continue
            if excl_overlap and (cum[i + 1] - cum[i - n_win + 1]) > 0:
                base[k] = "input_window_overlaps_event"; continue
        ev = ev_by_rec.get(rid)
        onsets = ev["onset_t"].to_numpy() if ev is not None else np.array([])
        ends = ev["end_t"].to_numpy() if ev is not None else np.array([])
        eids = ev["event_id"].to_numpy() if ev is not None else np.array([])
        t_anchor = ts[all_idx]
        if min_since > 0 and len(ends):
            for k, t in enumerate(t_anchor):
                if base[k] == R_OK:
                    prev = ends[ends < t]
                    if len(prev) and t - prev.max() < min_since:
                        base[k] = "too_soon_after_previous_event"
        task = g[cols["task"]].iloc[0]
        if eligible_tasks is not None and task not in set(eligible_tasks):
            base[:] = np.where(base == R_OK, "task_not_eligible", base)
        if eligible_activities is not None and activity_col:
            act = g[activity_col].to_numpy()[all_idx]
            ok = np.isin(act, list(eligible_activities))
            base[:] = np.where((base == R_OK) & ~ok, "activity_not_eligible", base)
        frame = pd.DataFrame({
            "recording_id": rid, "participant_id": g[cols["participant"]].iloc[0], "session_id": g[cols["session"]].iloc[0],
            "task_id": task, "anchor_idx": all_idx, "anchor_t": t_anchor,
            "window_start_t": np.where(hist, ts[np.maximum(all_idx - n_win + 1, 0)], np.nan),
            "n_window_samples": n_win, "dt_s": dt, "base_status": base})
        t_end = ts[-1]
        for H in horizons:
            key = _hkey(H)
            status = np.where(base == R_OK, "eligible", "excluded_base").astype(object)
            y = np.zeros(len(all_idx), dtype=int)
            ev_id = np.full(len(all_idx), "", dtype=object)
            lead_to_onset = np.full(len(all_idx), np.nan)
            for k, t in enumerate(t_anchor):
                if status[k] != "eligible":
                    continue
                if full_future and t + H > t_end + 1e-9:
                    status[k] = "censored_future"; continue
                hit = np.where((onsets > t) & (onsets <= t + H + 1e-9))[0]
                if len(hit):
                    y[k] = 1; ev_id[k] = eids[hit[0]]; lead_to_onset[k] = onsets[hit[0]] - t
            frame[f"{key}_status"] = status
            frame[f"{key}_y"] = y
            frame[f"{key}_event_id"] = ev_id
            frame[f"{key}_time_to_onset"] = lead_to_onset
        out.append(frame)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def _hkey(H: float) -> str:
    return "h" + format(float(H), "g").replace(".", "p")


def horizon_key(H: float) -> str:
    return _hkey(H)


def summarize_targets(anchors: pd.DataFrame, events: pd.DataFrame, horizons: list[float], stride_s: float) -> dict:
    out: dict = {"n_anchors_total": int(len(anchors)), "base_status_counts": anchors["base_status"].value_counts().to_dict(),
                 "n_events_total": int(len(events)), "horizons": {}}
    for H in horizons:
        k = _hkey(H)
        st = anchors[f"{k}_status"]
        el = anchors[st == "eligible"]
        pos_ev = set(el.loc[el[f"{k}_y"] == 1, f"{k}_event_id"])
        n_pos, n_neg = int((el[f"{k}_y"] == 1).sum()), int((el[f"{k}_y"] == 0).sum())
        # why events contribute no positive anchor
        reasons: dict[str, int] = {}
        for _, e in events.iterrows():
            if e["event_id"] in pos_ev:
                continue
            reasons[_event_exclusion_reason(e, anchors, H)] = reasons.get(_event_exclusion_reason(e, anchors, H), 0) + 1
        out["horizons"][k] = {
            "horizon_s": H, "status_counts": st.value_counts().to_dict(),
            "n_eligible": int(len(el)), "n_positive": n_pos, "n_negative": n_neg,
            "positive_fraction": float(n_pos / max(1, n_pos + n_neg)),
            "n_events_with_positive_anchor": int(len(pos_ev)),
            "n_events_without_positive_anchor": int(len(events) - len(pos_ev)),
            "event_exclusion_reasons": reasons,
            "n_participants_eligible": int(el["participant_id"].nunique()),
            "n_participants_with_positive": int(el.loc[el[f"{k}_y"] == 1, "participant_id"].nunique()),
            "monitored_time_s_eligible": float(len(el) * stride_s)}
    return out


def _event_exclusion_reason(e: pd.Series, anchors: pd.DataFrame, H: float) -> str:
    k = _hkey(H)
    a = anchors[(anchors["recording_id"] == e["recording_id"]) & (anchors["anchor_t"] < e["onset_t"]) &
                (anchors["anchor_t"] >= e["onset_t"] - H)]
    if a.empty:
        return "no_anchor_in_prewarning_interval"
    st = set(a["base_status"])
    if st == {"insufficient_history"} or (st <= {"insufficient_history"}):
        return "insufficient_history_before_onset"
    if "input_window_overlaps_event" in st or "ongoing_event_at_anchor" in st:
        if not (st - {"input_window_overlaps_event", "ongoing_event_at_anchor", "insufficient_history"}):
            return "preceded_by_another_event"
    return "mixed_exclusions"
