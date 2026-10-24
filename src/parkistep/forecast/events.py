"""Freezing-event extraction from the sample-level annotation. Events never cross recording boundaries."""
from __future__ import annotations

import numpy as np
import pandas as pd


def extract_events(sensor: pd.DataFrame, *, recording_col: str, participant_col: str, session_col: str, task_col: str,
                   time_col: str, label_col: str, positive_value: int, severity_col: str | None = None,
                   merge_gap_s: float = 0.0, min_duration_s: float = 0.0) -> pd.DataFrame:
    """Return one row per annotated event: a contiguous run of `label == positive_value` inside ONE recording.

    Onset = timestamp of the first positive sample; end = timestamp of the last positive sample.
    Adjacent events are merged only when `merge_gap_s` > 0 and the gap between them is <= that value.
    """
    if merge_gap_s < 0 or min_duration_s < 0:
        raise ValueError("merge_gap_s and min_duration_s must be >= 0")
    rows = []
    for rid, g in sensor.groupby(recording_col, sort=False):
        lab = (g[label_col].to_numpy() == positive_value)
        if not lab.any():
            continue
        ts = g[time_col].to_numpy(dtype=float)
        sev = g[severity_col].to_numpy() if severity_col else None
        d = np.diff(np.r_[0, lab.astype(int), 0])
        starts, ends = np.where(d == 1)[0], np.where(d == -1)[0] - 1      # inclusive sample indices
        runs = list(zip(starts.tolist(), ends.tolist()))
        if merge_gap_s > 0 and len(runs) > 1:
            merged = [runs[0]]
            for s, e in runs[1:]:
                if ts[s] - ts[merged[-1][1]] <= merge_gap_s:
                    merged[-1] = (merged[-1][0], e)
                else:
                    merged.append((s, e))
            runs = merged
        n = len(g)
        for s, e in runs:
            dur = float(ts[e] - ts[s])
            dt = float(np.median(np.diff(ts))) if n > 1 else 0.0
            dur_incl = dur + dt                                                 # span including the last sample's interval
            if dur_incl < min_duration_s:
                continue
            vals = sev[s:e + 1] if sev is not None else None
            sv = vals[vals > 0] if vals is not None else None
            rows.append({
                "recording_id": rid, "participant_id": g[participant_col].iloc[0], "session_id": g[session_col].iloc[0],
                "task_id": g[task_col].iloc[0], "onset_idx": s, "end_idx": e, "onset_t": float(ts[s]), "end_t": float(ts[e]),
                "duration_s": dur_incl, "recording_start_t": float(ts[0]), "recording_end_t": float(ts[-1]),
                "touches_recording_end": bool(e == n - 1),
                "severity_max": int(sv.max()) if sv is not None and len(sv) else None,
                "severity_n_values": int(len(np.unique(sv))) if sv is not None and len(sv) else None,
            })
    ev = pd.DataFrame(rows)
    if ev.empty:
        return ev
    ev = ev.reset_index(drop=True)
    ev.insert(0, "event_id", [f"{r}-e{k}" for k, r in zip(ev.groupby("recording_id").cumcount(), ev["recording_id"])])
    return ev
