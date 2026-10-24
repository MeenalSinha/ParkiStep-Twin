"""Data-quality statistics and threshold-based checks, computed from the loaded data only."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import channel_specs
from .issues import Issues, severity_from_config


def _f(x):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else float(x)


def recording_index(df: pd.DataFrame, group_by: list[str]) -> pd.Series:
    return df.groupby(group_by, sort=False, observed=True).ngroup()


def clinical_stats(df: pd.DataFrame, profile: dict) -> dict:
    key = profile["clinical"]["key"]
    fields = profile["clinical"]["fields"]
    out = {"n_records": int(len(df)), "n_distinct_participants": int(df[key].nunique()) if key in df else None,
           "duplicate_key_rows": int(df[key].dropna().duplicated(keep=False).sum()) if key in df else None,
           "fields": {}}
    for canon, spec in fields.items():
        if canon not in df.columns:
            continue
        col = df[canon]
        entry = {"dtype": str(col.dtype), "missing": int(col.isna().sum()),
                 "missing_pct": float(100 * col.isna().mean()), "unit": spec.get("unit")}
        if pd.api.types.is_numeric_dtype(col) and canon != key:
            d = col.astype("float64").describe()
            entry["describe"] = {k: _f(d[k]) for k in ("count", "mean", "std", "min", "25%", "50%", "75%", "max")}
        elif not pd.api.types.is_numeric_dtype(col):
            entry["value_counts"] = {str(k): int(v) for k, v in col.value_counts(dropna=False).items()}
        out["fields"][canon] = entry
    return out


def sensor_stats(df: pd.DataFrame, profile: dict, rcfg: dict, issues: Issues) -> dict:
    s = profile["sensor"]
    vcfg = profile.get("validation", {}).get("sensor", {})
    ts = s["timestamp"]["canonical"]
    group_by = s["group_by"]
    chans = channel_specs(profile)
    names = [c["canonical"] for c in chans]
    gid = recording_index(df, group_by)
    pk = profile["join"]["key"]
    out: dict = {"n_rows": int(len(df)), "n_recordings": int(gid.nunique()),
                 "n_participants": int(df[pk].nunique())}
    for g in group_by:
        out[f"n_distinct_{g}"] = int(df[g].nunique())

    # ---- missing ------------------------------------------------------
    miss = df[names].isna()
    per_ch = {n: {"missing": int(miss[n].sum()), "missing_pct": float(100 * miss[n].mean())} for n in names}
    positions = {}
    for pos in dict.fromkeys(c["position"] for c in chans):
        cols = [c["canonical"] for c in chans if c["position"] == pos]
        allm = miss[cols].all(axis=1)
        anym = miss[cols].any(axis=1)
        positions[pos] = {"rows_all_channels_missing": int(allm.sum()),
                          "rows_partially_missing": int((anym & ~allm).sum())}
    out["missing"] = {"per_channel": per_ch, "per_position": positions,
                      "rows_with_any_missing": int(miss.any(axis=1).sum()),
                      "rows_with_any_missing_pct": float(100 * miss.any(axis=1).mean()),
                      "total_missing_cells": int(miss.to_numpy().sum())}
    m_sev = severity_from_config(vcfg.get("missing_value_severity"), "warning")
    if out["missing"]["total_missing_cells"]:
        issues.add(m_sev, "SENSOR_MISSING_VALUES",
                   f"{out['missing']['rows_with_any_missing']:,} of {len(df):,} sensor rows "
                   f"({out['missing']['rows_with_any_missing_pct']:.1f}%) have at least one missing channel value; "
                   "retained as missing (no imputation or deletion).", location="sensor channels",
                   action="Phase 2 must define an explicit policy for windows containing missing samples.",
                   rows=out["missing"]["rows_with_any_missing"])
    allm_sev = severity_from_config(vcfg.get("all_missing_channel_severity"), "error")
    for n in names:
        if per_ch[n]["missing"] == len(df):
            issues.add(allm_sev, "SENSOR_CHANNEL_ALL_MISSING", f"Channel '{n}' has no valid values.", location=n,
                       action="Check the column mapping or the source file.")

    # ---- constant / implausible ---------------------------------------
    c_sev = severity_from_config(vcfg.get("constant_channel_severity"), "warning")
    imp_sev = severity_from_config(vcfg.get("implausible_value_severity"), "warning")
    constant, implausible = [], {}
    for c in chans:
        col = df[c["canonical"]]
        if col.notna().any() and col.std(skipna=True) == 0:
            constant.append(c["canonical"])
        rng = c.get("plausible_range")
        if rng:
            n_out = int(((col < rng[0]) | (col > rng[1])).sum())
            if n_out:
                implausible[c["canonical"]] = {"count": n_out, "range": rng}
    out["constant_channels"] = constant
    out["implausible_values"] = implausible
    if constant:
        issues.add(c_sev, "SENSOR_CHANNEL_CONSTANT", f"Constant-value channel(s): {constant}.", location="sensor channels")
    if implausible:
        issues.add(imp_sev, "SENSOR_IMPLAUSIBLE_VALUES",
                   f"{len(implausible)} channel(s) have values outside the configured plausible range: {list(implausible)[:6]}.",
                   location="sensor.channels.signals.*.plausible_range",
                   action="Review; the ranges are configurable review heuristics, values are not clipped.")
    out["channel_inventory"] = [{"name": c["canonical"], "source": c["source"], "unit": c["unit"],
                                 "dtype": str(df[c["canonical"]].dtype)} for c in chans]

    out["sampling"] = sampling_stats(df, gid, ts, profile, rcfg, issues)
    # recording coverage
    cov = df.groupby(group_by, sort=True, observed=True)[ts].agg(["size", "min", "max"])
    cov["duration_s"] = cov["max"] - cov["min"]
    out["coverage"] = {"n_recordings": int(len(cov)),
                       "total_duration_s": float(cov["duration_s"].sum()),
                       "recording_duration_s": {k: float(v) for k, v in cov["duration_s"].describe().items()},
                       "rows_per_participant": {str(k): int(v) for k, v in df.groupby(pk).size().items()}}
    return out


def sampling_stats(df, gid, ts, profile, rcfg, issues) -> dict:
    s = profile["sampling"]
    vcfg = profile.get("validation", {}).get("sensor", {})
    group_by = profile["sensor"]["group_by"]
    dt = df.groupby(gid, sort=False)[ts].diff()
    out: dict = {"timestamps_missing": int(df[ts].isna().sum())}
    if out["timestamps_missing"]:
        issues.add("error", "TIMESTAMP_MISSING", f"{out['timestamps_missing']} row(s) have no valid timestamp.",
                   location=ts, action="Fix the timestamp column in the source.")
    dup_mask = df.duplicated(subset=group_by + [ts], keep=False)
    n_dup = int(df.duplicated(subset=group_by + [ts], keep="first").sum())
    out["duplicate_timestamps"] = n_dup
    if n_dup:
        issues.add(severity_from_config(vcfg.get("duplicate_timestamp_severity"), "error"), "TIMESTAMP_DUPLICATE",
                   f"{n_dup} duplicate (recording, timestamp) row(s) found.", location=ts,
                   action="Duplicates are not removed automatically; resolve them in the source or define a dedup policy.")
    ooo = int((dt < 0).sum())
    out["out_of_order_steps"] = ooo
    if ooo:
        issues.add(severity_from_config(vcfg.get("out_of_order_severity"), "error"), "TIMESTAMP_OUT_OF_ORDER",
                   f"{ooo} step(s) go backwards in time within a recording.", location=ts,
                   action="Rows are not re-sorted automatically; fix the source ordering.")
    blocks = int((gid != gid.shift()).sum())
    out["recording_blocks"] = blocks
    out["recordings_non_contiguous"] = blocks - int(gid.nunique())
    if out["recordings_non_contiguous"] > 0:
        issues.add(severity_from_config(vcfg.get("non_contiguous_recording_severity"), "warning"),
                   "RECORDING_NON_CONTIGUOUS",
                   f"{out['recordings_non_contiguous']} recording(s) are split into several row blocks.", location="group_by")
    # Boundary visibility: if consecutive recordings abut on the timestamp axis (one expected step apart), the
    # timestamp is a single running clock and recording boundaries cannot be recovered from timestamp gaps.
    firsts = df.groupby(gid, sort=False)[ts].first()
    lasts = df.groupby(gid, sort=False)[ts].last()
    if len(firsts) > 1:
        step = df.groupby(gid, sort=False)[ts].diff()
        ref_step = float(step[step > 0].median()) if (step > 0).any() else None
        if ref_step:
            bridge = (firsts.shift(-1) - lasts).dropna()
            n_abut = int((abs(bridge - ref_step) <= 0.05 * ref_step).sum())
            out["recording_boundaries"] = {"n_boundaries": int(len(bridge)), "abutting_on_shared_clock": n_abut}
            if n_abut > 0:
                issues.add("warning", "RECORDING_BOUNDARIES_INVISIBLE_IN_TIMESTAMPS",
                           f"{n_abut} of {len(bridge)} consecutive recording boundaries are exactly one sampling step apart: "
                           "the timestamp is one running clock across recordings, so gap checks cannot detect recording "
                           "boundaries.", location=ts,
                           action="Phase 2 windowing must group by the recording key (group_by), never by timestamp continuity alone.")
    pos = dt[dt > 0].dropna()
    if pos.empty:
        out["note"] = "No positive timestamp intervals; sampling statistics unavailable."
        return out
    q = pos.quantile([0.01, 0.05, 0.5, 0.95, 0.99])
    med = float(pos.median())
    out["interval_s"] = {"n": int(len(pos)), "min": float(pos.min()), "max": float(pos.max()), "mean": float(pos.mean()),
                         "std": float(pos.std()), "median": med,
                         "quantiles": {f"p{int(k*100):02d}": float(v) for k, v in q.items()}}
    out["estimated_hz_overall"] = 1.0 / med
    per_rec = dt.where(dt > 0).groupby(gid).median()
    out["estimated_hz_per_recording"] = {"min": float((1 / per_rec).min()), "max": float((1 / per_rec).max())}
    exp_hz = s.get("expected_hz")
    out["expected_hz"] = exp_hz
    ref_interval = (1.0 / exp_hz) if exp_hz else med
    out["gap_threshold_s"] = float(s.get("gap_threshold_factor", 2.0) * ref_interval)
    tol = float(s.get("tolerance_fraction", 0.05))
    out["fraction_within_tolerance"] = float((abs(pos - ref_interval) <= tol * ref_interval).mean())
    if exp_hz:
        rel = abs(1.0 / med - exp_hz) / exp_hz
        out["expected_vs_observed_relative_diff"] = float(rel)
        if rel > tol:
            issues.add(severity_from_config(s.get("mismatch_severity"), "warning"), "SAMPLING_RATE_MISMATCH",
                       f"Observed rate {1/med:.3f} Hz differs from the configured expectation {exp_hz} Hz by {100*rel:.1f}% "
                       f"(tolerance {100*tol:.0f}%). Timestamps are not altered. Check `sensor.timestamp.unit`.",
                       location="sampling.expected_hz")
    gaps = dt[dt > out["gap_threshold_s"]]
    out["gaps"] = {"count": int(len(gaps)), "total_missing_s": float((gaps - ref_interval).sum()) if len(gaps) else 0.0}
    top_n = int(rcfg.get("top_n_gaps", 10))
    if len(gaps):
        top = gaps.sort_values(ascending=False).head(top_n)
        out["gaps"]["largest"] = [{"row": int(i), "recording_id": str(df.loc[i, "recording_id"]) if "recording_id" in df else None,
                                   "gap_s": float(v)} for i, v in top.items()]
        issues.add("warning", "SAMPLING_GAPS", f"{len(gaps)} gap(s) longer than {out['gap_threshold_s']:.4f}s detected.",
                   location="sampling.gap_threshold_factor", action="Phase 2 windowing must not span these gaps.")
    bins = int(rcfg.get("histogram_bins", 20))
    lo, hi = float(pos.min()), float(pos.max())
    if hi > lo:
        h, e = np.histogram(pos, bins=bins)
        out["interval_histogram"] = {"counts": h.tolist(), "edges": [float(x) for x in e]}
    return out


def annotation_stats(df: pd.DataFrame, profile: dict, issues: Issues) -> dict:
    s = profile["sensor"]
    ann = s.get("annotations", {})
    rules = s.get("annotation_rules", {})
    vcfg = profile.get("validation", {})
    ts = s["timestamp"]["canonical"]
    group_by = s["group_by"]
    out: dict = {"fields": {}, "documented_by_source": bool(rules.get("documented_by_source", False))}
    if ann and not out["documented_by_source"]:
        issues.add("warning", "ANNOTATION_SEMANTICS_UNDOCUMENTED",
                   "The profile does not state that annotation semantics are documented by the source.",
                   location="sensor.annotation_rules.documented_by_source")
    undoc_sev = severity_from_config(vcfg.get("sensor", {}).get("undocumented_label_severity"), "warning")
    miss_sev = severity_from_config(vcfg.get("annotation", {}).get("missing_label_severity"), "warning")
    for name, spec in ann.items():
        if name not in df.columns:
            continue
        col = df[name]
        allowed = set(spec.get("allowed_values", []))
        if "no_event_value" in spec:
            allowed.add(spec["no_event_value"])
        counts = {str(k): int(v) for k, v in col.value_counts(dropna=True).sort_index().items()}
        unexpected = sorted(int(v) if float(v).is_integer() else float(v) for v in col.dropna().unique() if v not in allowed) \
            if allowed else []
        n_miss = int(col.isna().sum())
        out["fields"][name] = {"role": spec.get("role"), "value_counts": counts,
                               "value_pct": {k: 100 * v / len(df) for k, v in counts.items()},
                               "labels": {str(k): v for k, v in (spec.get("labels") or {}).items()},
                               "missing": n_miss, "unexpected_values": unexpected,
                               "rows_with_unexpected_values": int(col.isin(unexpected).sum()) if unexpected else 0}
        if unexpected:
            issues.add(undoc_sev, "ANNOTATION_UNDOCUMENTED_VALUE",
                       f"'{name}' contains value(s) {unexpected} not listed in the documentation/profile "
                       f"({out['fields'][name]['rows_with_unexpected_values']:,} rows). Kept unchanged.",
                       location=f"sensor.annotations.{name}.allowed_values",
                       action="Confirm their meaning with the dataset authors before using this label in Phase 2.",
                       values=unexpected)
        if n_miss:
            issues.add(miss_sev, "ANNOTATION_MISSING", f"'{name}' is missing for {n_miss:,} rows.", location=name)

    ev = rules.get("event_label")
    if ev and ev in df.columns:
        pos_val = rules.get("positive_value", 1)
        gid = recording_index(df, group_by)
        flag = (df[ev] == pos_val).astype("int8")
        prev = flag.groupby(gid).shift(1).fillna(0)
        dt = df.groupby(gid, sort=False)[ts].diff()
        gap_thr = None
        run_start = (flag == 1) & ((prev == 0))
        ep_id = run_start.groupby(gid).cumsum().where(flag == 1)
        episodes = df.assign(_gid=gid, _ep=ep_id)[flag == 1].groupby(["_gid", "_ep"], sort=False)
        n_ep = int(episodes.ngroups)
        durs = (episodes[ts].agg(lambda x: x.max() - x.min()) + (dt.median() if dt.notna().any() else 0)) if n_ep else pd.Series(dtype=float)
        sample_counts = episodes.size()
        per_part = {}
        pk = profile["join"]["key"]
        if n_ep and pk in df:
            pp = episodes[pk].first()
            per_part = {str(k): int(v) for k, v in pp.value_counts().sort_index().items()}
        out["episodes"] = {
            "definition": f"contiguous run of '{ev}' == {pos_val} within one recording (group_by={group_by})",
            "count": n_ep, "per_participant": per_part,
            "participants_with_episodes": len(per_part),
            "positive_rows": int(flag.sum()), "positive_pct": float(100 * flag.mean()),
            "duration_s": {k: float(v) for k, v in durs.describe().items()} if n_ep else {},
            "duration_samples": {k: float(v) for k, v in sample_counts.describe().items()} if n_ep else {},
            "starting_at_recording_start": int(((flag == 1) & (prev == 0) & gid.ne(gid.shift())).sum()),
            "note": "Durations include one sample interval; they describe source annotations only, not forecasting labels."}
        # severity consistency
        sev_name = rules.get("severity")
        if sev_name and sev_name in df.columns and rules.get("severity_requires_event", False):
            no_ev = ann[sev_name].get("no_event_value", 0)
            bad_a = int(((df[sev_name] != no_ev) & df[sev_name].notna() & (flag == 0)).sum())
            bad_b = int(((df[sev_name] == no_ev) & (flag == 1)).sum())
            out["severity_consistency"] = {"severity_without_event": bad_a, "event_without_severity": bad_b}
            if bad_a or bad_b:
                issues.add(severity_from_config(vcfg.get("annotation", {}).get("severity_inconsistency_severity"), "warning"),
                           "ANNOTATION_SEVERITY_INCONSISTENT",
                           f"Severity/event disagreement: {bad_a:,} rows with severity but no event, {bad_b:,} event rows without severity.",
                           location=f"{sev_name} vs {ev}")
            ep_sev = df.assign(_gid=gid, _ep=ep_id)[flag == 1].groupby(["_gid", "_ep"])[sev_name].nunique() if n_ep else pd.Series(dtype=int)
            out["severity_consistency"]["episodes_with_multiple_severity_values"] = int((ep_sev > 1).sum())
        # alignment with annotation frame rate (video fps)
        res = rules.get("annotation_resolution_hz")
        exp_hz = profile["sampling"].get("expected_hz")
        if res and exp_hz:
            per = exp_hz / res
            if abs(per - round(per)) < 1e-9 and per >= 1:
                per = int(round(per))
                change = (df[ev] != df[ev].groupby(gid).shift(1)) | gid.ne(gid.shift())
                run_id = change.cumsum()
                lens = df.groupby(run_id).size()
                out["annotation_alignment"] = {
                    "samples_per_annotation_frame": per,
                    "label_runs": int(len(lens)),
                    "runs_whose_length_is_multiple_of_frame": int((lens % per == 0).sum()),
                    "fraction_multiple_of_frame": float((lens % per == 0).mean()),
                    "note": "Informational: label runs are expected to align with annotation frames only approximately."}
    return out


def readiness_checks(profile: dict, ann: dict, issues: Issues) -> list[dict]:
    r = profile.get("readiness") or {}
    sev = severity_from_config(r.get("severity"), "error")
    checks = []
    ep = ann.get("episodes")
    n = ep["count"] if ep else 0
    np_ = ep["participants_with_episodes"] if ep else 0
    mt = r.get("min_episodes_total")
    mp = r.get("min_participants_with_episodes")
    if mt is not None:
        ok = n >= mt
        checks.append({"check": "annotated_episodes_available", "required": mt, "observed": n, "passed": ok})
        if not ok:
            issues.add(sev, "READINESS_TOO_FEW_EPISODES", f"Only {n} annotated episode(s); at least {mt} required for the forecasting task.",
                       location="readiness.min_episodes_total")
    if mp is not None:
        ok = np_ >= mp
        checks.append({"check": "participants_with_episodes", "required": mp, "observed": np_, "passed": ok})
        if not ok:
            issues.add(sev, "READINESS_TOO_FEW_PARTICIPANTS_WITH_EPISODES",
                       f"Only {np_} participant(s) have annotated episodes; at least {mp} required for participant-wise evaluation.",
                       location="readiness.min_participants_with_episodes")
    return checks
