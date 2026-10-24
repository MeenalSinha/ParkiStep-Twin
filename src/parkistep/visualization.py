"""Configurable exploratory figures generated from the loaded data (never simulated values)."""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from .config import Settings, channel_specs
from .pipeline import AuditResult

log = logging.getLogger(__name__)


def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


def _select(keys: list, policy: str, count: int, sizes: dict) -> list:
    keys = sorted(keys, key=str)
    if policy == "first":
        return keys[:count]
    if policy == "longest":
        return sorted(keys, key=lambda k: (-sizes[k], str(k)))[:count]
    if policy == "shortest":
        return sorted(keys, key=lambda k: (sizes[k], str(k)))[:count]
    if policy == "evenly_spaced":
        if count >= len(keys):
            return keys
        idx = np.linspace(0, len(keys) - 1, count).round().astype(int)
        return [keys[i] for i in idx]
    raise ValueError(f"unknown selection policy {policy!r}; use first|longest|shortest|evenly_spaced")


def generate_figures(settings: Settings, result: AuditResult) -> dict:
    vc = settings.project.get("visualization", {})
    out = {"generated": {}, "skipped": {}}
    if not vc.get("enabled", True):
        out["skipped"]["all"] = "visualization.enabled is false"
        return out
    if result.sensor is None or result.clinical is None:
        out["skipped"]["all"] = "no validated data available (audit status %s)" % result.status
        return out
    try:
        plt = _plt()
    except ImportError:
        out["skipped"]["all"] = "matplotlib is not installed (pip install matplotlib)"
        return out
    fdir = settings.figures_dir
    fdir.mkdir(parents=True, exist_ok=True)
    fmt, dpi = vc.get("format", "png"), int(vc.get("dpi", 110))
    prof = settings.dataset
    sens, clin, rep = result.sensor, result.clinical, result.report
    ts = prof["sensor"]["timestamp"]["canonical"]
    gb = prof["sensor"]["group_by"]
    pk = prof["join"]["key"]
    chans = {c["canonical"]: c for c in channel_specs(prof)}
    want_ch = [c for c in vc.get("channels", []) if c in chans]
    ann = prof["sensor"].get("annotations", {})
    ev = prof["sensor"].get("annotation_rules", {}).get("event_label")
    tag = " [SYNTHETIC TEST DATA]" if prof.get("synthetic") else ""
    sel = vc.get("selection", {})
    maxpts = int(vc.get("max_plot_points", 20000))

    def save(name, fig):
        p = fdir / f"{name}.{fmt}"
        fig.tight_layout()
        fig.savefig(p, dpi=dpi, bbox_inches="tight")
        plt.close(fig)
        out["generated"][name] = str(p)

    recs = {k: g for k, g in sens.groupby(gb, sort=True, observed=True)}
    sizes = {k: len(g) for k, g in recs.items()}

    for name in vc.get("figures", []):
        try:
            if name == "missing_values":
                miss = pd.concat([sens[list(chans)].isna().mean() * 100,
                                  clin.drop(columns=["source_row"]).isna().mean() * 100])
                fig, ax = plt.subplots(figsize=(8, max(4, 0.22 * len(miss))))
                ax.barh(miss.index, miss.values)
                ax.set_xlabel("Missing values (%)"); ax.set_title("Missing values by column" + tag)
                ax.invert_yaxis(); save(name, fig)
            elif name == "sampling_intervals":
                gid = sens.groupby(gb, sort=False, observed=True).ngroup()
                dt = sens.groupby(gid, sort=False)[ts].diff().dropna()
                dt = dt[dt > 0]
                if dt.empty:
                    out["skipped"][name] = "no positive timestamp intervals"; continue
                fig, ax = plt.subplots(figsize=(7, 4))
                ax.hist(dt, bins=60); ax.set_yscale("log")
                exp = prof["sampling"].get("expected_hz")
                if exp:
                    ax.axvline(1 / exp, color="r", ls="--", label=f"expected 1/{exp} s")
                    ax.legend()
                ax.set_xlabel("Sample interval (s)"); ax.set_ylabel("Count (log)")
                ax.set_title("Observed sampling intervals" + tag); save(name, fig)
            elif name == "channel_traces":
                if not want_ch:
                    out["skipped"][name] = "none of visualization.channels exist in the profile"; continue
                keys = _select(list(recs), sel.get("policy", "longest"), int(sel.get("count", 2)), sizes)
                fig, axes = plt.subplots(len(keys), 1, figsize=(10, 3 * len(keys)), squeeze=False)
                for ax, k in zip(axes[:, 0], keys):
                    g = recs[k]
                    step = max(1, len(g) // maxpts)
                    g = g.iloc[::step]
                    t = g[ts] - g[ts].iloc[0]
                    for c in want_ch:
                        ax.plot(t, g[c], lw=0.6, label=f"{c} [{chans[c]['unit']}]")
                    if ev in g:
                        ax.fill_between(t, 0, 1, where=(g[ev] == 1), transform=ax.get_xaxis_transform(), alpha=0.2, color="r", label=ev)
                    ax.set_title(f"Recording {dict(zip(gb, k if isinstance(k, tuple) else (k,)))}" + tag, fontsize=9)
                    ax.set_xlabel("Time in recording (s)")
                axes[0, 0].legend(fontsize=6, ncol=3); save(name, fig)
            elif name == "clinical_distributions":
                cols = [c for c in clin.columns if c not in (pk, "source_row") and pd.api.types.is_numeric_dtype(clin[c])]
                if not cols:
                    out["skipped"][name] = "no numeric clinical fields"; continue
                n = len(cols); ncol = 3; nrow = int(np.ceil(n / ncol))
                fig, axes = plt.subplots(nrow, ncol, figsize=(10, 2.8 * nrow), squeeze=False)
                for ax, c in zip(axes.ravel(), cols):
                    unit = prof["clinical"]["fields"][c].get("unit")
                    ax.hist(clin[c].dropna(), bins=min(10, max(3, clin[c].nunique())))
                    ax.set_title(f"{c} (n={int(clin[c].notna().sum())})", fontsize=9)
                    if unit: ax.set_xlabel(unit)
                for ax in axes.ravel()[n:]:
                    ax.axis("off")
                fig.suptitle("Clinical variable distributions" + tag); save(name, fig)
            elif name == "label_distribution":
                names = [a for a in ann if a in sens.columns]
                if not names:
                    out["skipped"][name] = "no annotation columns"; continue
                fig, axes = plt.subplots(1, len(names), figsize=(4 * len(names), 3.5), squeeze=False)
                for ax, a in zip(axes[0], names):
                    vc_ = sens[a].value_counts().sort_index()
                    labels = ann[a].get("labels") or {}
                    ax.bar([labels.get(int(k), str(k)) if not pd.isna(k) else "NA" for k in vc_.index], vc_.values)
                    ax.set_title(a); ax.set_ylabel("Samples"); ax.tick_params(axis="x", rotation=60)
                fig.suptitle("Observed annotation distributions" + tag); save(name, fig)
            elif name == "event_traces":
                if not ev or ev not in sens.columns or not want_ch:
                    out["skipped"][name] = "event label or channels unavailable"; continue
                ctx = float(vc.get("event_context_seconds", 3.0))
                events = []
                for k, g in recs.items():
                    f = (g[ev] == 1).astype(int).to_numpy()
                    starts = np.flatnonzero(np.diff(np.r_[0, f]) == 1)
                    ends = np.flatnonzero(np.diff(np.r_[f, 0]) == -1)
                    for s_, e_ in zip(starts, ends):
                        events.append((k, int(s_), int(e_), e_ - s_ + 1))
                if not events:
                    out["skipped"][name] = "no annotated events"; continue
                events.sort(key=lambda x: (-x[3], str(x[0]), x[1]))      # deterministic: longest first
                events = events[: int(vc.get("max_events", 3))]
                fig, axes = plt.subplots(len(events), 1, figsize=(10, 3 * len(events)), squeeze=False)
                for ax, (k, s_, e_, _) in zip(axes[:, 0], events):
                    g = recs[k]
                    t = g[ts].to_numpy()
                    lo, hi = t[s_] - ctx, t[e_] + ctx
                    m = (t >= lo) & (t <= hi)
                    for c in want_ch:
                        ax.plot(t[m] - t[s_], g[c].to_numpy()[m], lw=0.7, label=f"{c} [{chans[c]['unit']}]")
                    ax.axvspan(0, t[e_] - t[s_], color="r", alpha=0.2, label="annotated event")
                    ax.set_title(f"Event in recording {k}" + tag, fontsize=9); ax.set_xlabel("Time relative to annotated onset (s)")
                axes[0, 0].legend(fontsize=6, ncol=3); save(name, fig)
            elif name == "recording_durations":
                dur = sens.groupby(gb, observed=True)[ts].agg(lambda x: x.max() - x.min()).groupby(level=0).sum()
                fig, ax = plt.subplots(figsize=(9, 4))
                ax.bar([str(i) for i in dur.index], dur.values)
                ax.set_xlabel(pk); ax.set_ylabel("Recorded duration (s)")
                ax.set_title("Total recording duration by participant" + tag); save(name, fig)
            elif name == "stream_coverage":
                rows = sens.groupby(pk).size()
                ids = sorted(set(rows.index) | set(clin[pk].dropna()))
                fig, ax = plt.subplots(figsize=(9, 4))
                colors = ["tab:green" if i in set(clin[pk].dropna()) else "tab:red" for i in ids]
                ax.bar([str(i) for i in ids], [int(rows.get(i, 0)) for i in ids], color=colors)
                ax.set_xlabel(pk); ax.set_ylabel("Sensor rows")
                ax.set_title("Stream coverage (green: clinical record present, red: absent)" + tag); save(name, fig)
            else:
                out["skipped"][name] = "unknown figure name"
        except Exception as exc:  # a failing plot must not hide the audit
            log.exception("Figure %s failed", name)
            out["skipped"][name] = f"failed: {exc}"
    return out
