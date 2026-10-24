"""Presentation data for charts, independent of any plotting library (so it can be unit-tested without Plotly).

All values come from observations the engine accepted. Decimation is for rendering only; gaps (time steps much larger than the
typical step) become explicit NaN breaks so a dropout is shown as a dropout, not bridged by a line.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from ..config import channel_specs


def decimate(df: pd.DataFrame, max_points: int) -> pd.DataFrame:
    """Keep at most `max_points` rows by uniform stride (always keeps the first and the last row)."""
    n = len(df)
    if max_points <= 0 or n <= max_points:
        return df
    stride = math.ceil(n / max_points)
    idx = np.unique(np.r_[np.arange(0, n, stride), n - 1])
    return df.iloc[idx]


def insert_gaps(df: pd.DataFrame, time_col: str, factor: float, reference_step: float | None = None) -> pd.DataFrame:
    """Insert a NaN row after every sample followed by a step larger than `factor` x the reference step.

    The reference step is the median step of the full (undecimated) data when given, otherwise of `df`."""
    if len(df) < 3:
        return df
    t = df[time_col].to_numpy(dtype=float)
    d = np.diff(t)
    ref = reference_step if reference_step and reference_step > 0 else float(np.median(d))
    if not np.isfinite(ref) or ref <= 0:
        return df
    big = np.where(d > factor * ref)[0]
    if len(big) == 0:
        return df
    pieces, last = [], 0
    for i in big:
        pieces.append(df.iloc[last:i + 1])
        nan_row = {c: np.nan for c in df.columns}
        nan_row[time_col] = (t[i] + t[i + 1]) / 2.0
        pieces.append(pd.DataFrame([nan_row]))
        last = i + 1
    pieces.append(df.iloc[last:])
    return pd.concat(pieces, ignore_index=True)


def channel_catalog(profile: dict) -> list[dict]:
    """Channel metadata (position, signal, axis, unit) from the dataset profile - never typed into a page."""
    return [{k: c.get(k) for k in ("canonical", "position", "signal", "axis", "unit")} for c in channel_specs(profile)]


def select_channels(catalog: list[dict], signals: list[str] | None = None, positions: list[str] | None = None,
                    present: list[str] | None = None) -> list[dict]:
    out = [c for c in catalog if (not signals or c["signal"] in signals) and (not positions or c["position"] in positions)]
    if present is not None:
        ps = set(present)
        out = [c for c in out if c["canonical"] in ps]
    return out


def signal_traces(df: pd.DataFrame, channels: list[dict], *, time_col: str = "timestamp_s", max_points: int = 1500,
                  gap_factor: float = 2.0) -> list[dict]:
    """One trace per channel: x (seconds on the observation clock), y (NaN where data are missing or a gap was detected)."""
    if df is None or len(df) == 0:
        return []
    full_step = float(np.median(np.diff(df[time_col].to_numpy(dtype=float)))) if len(df) > 2 else None
    cols = [c["canonical"] for c in channels if c["canonical"] in df.columns]
    d = decimate(df[[time_col, *cols]], max_points)
    d = insert_gaps(d, time_col, gap_factor, full_step)
    out = []
    for c in channels:
        k = c["canonical"]
        if k not in d.columns:
            continue
        y = d[k].to_numpy(dtype=float)
        out.append({"channel": k, "position": c["position"], "signal": c["signal"], "axis": c["axis"], "unit": c.get("unit"),
                    "x": d[time_col].to_numpy(dtype=float), "y": y, "n_missing": int(np.isnan(y).sum())})
    return out


def coverage_summary(df: pd.DataFrame, channels: list[str], *, time_col: str = "timestamp_s", gap_factor: float = 2.0) -> dict:
    """Missing-value counts and gap list of the displayed window (descriptive, no imputation)."""
    if df is None or len(df) == 0:
        return {"n": 0, "duration_s": 0.0, "gaps": [], "missing_by_channel": {}}
    t = df[time_col].to_numpy(dtype=float)
    d = np.diff(t)
    ref = float(np.median(d)) if len(d) else None
    gaps = []
    if ref and ref > 0:
        for i in np.where(d > gap_factor * ref)[0]:
            gaps.append({"start_s": float(t[i]), "end_s": float(t[i + 1]), "duration_s": float(d[i])})
    miss = {c: int(df[c].isna().sum()) for c in channels if c in df.columns}
    return {"n": int(len(df)), "duration_s": float(t[-1] - t[0]), "median_step_s": ref, "gaps": gaps,
            "missing_by_channel": {k: v for k, v in miss.items() if v}}
