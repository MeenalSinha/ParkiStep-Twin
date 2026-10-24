"""Causal, deterministic window features. Input is only the trailing window; annotations are never an input."""
from __future__ import annotations

import fnmatch
import warnings
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..config import channel_specs
from .settings import FREQ_FEATURES, TIME_FEATURES

EPS = 1e-12


@dataclass
class FeatureSpec:
    raw_channels: list[str]                    # canonical raw channel names, in a fixed order
    positions: list[str]                       # all sensor positions of the dataset profile
    channel_meta: dict[str, dict]              # channel -> {position, signal, axis}
    derived: list[dict]                        # [{name, kind, members}]
    time_domain: list[str]
    freq: dict
    cross: list[dict]
    exclude: list[str] = field(default_factory=list)
    names: list[str] = field(default_factory=list)
    all_channels: list[str] = field(default_factory=list)

    def position_of(self, feature_name: str) -> str | None:
        ch = feature_name.split("__", 1)[0]
        return self.channel_meta.get(ch, {}).get("position")

    def to_dict(self) -> dict:
        return {"raw_channels": self.raw_channels, "positions": self.positions, "derived": self.derived,
                "time_domain": self.time_domain, "freq": self.freq, "cross": self.cross, "exclude": self.exclude,
                "feature_names": self.names}


def spec_from_dict(d: dict, profile_channels: dict) -> FeatureSpec:
    """Rebuild a FeatureSpec from a saved manifest (the feature definitions travel with the model)."""
    meta = {k: dict(v) for k, v in profile_channels.items()}
    for dd in d["derived"]:
        first = meta[dd["members"][0]]
        meta[dd["name"]] = {"position": first["position"], "signal": first["signal"], "axis": "mag"}
    return FeatureSpec(d["raw_channels"], d["positions"], meta, d["derived"], d["time_domain"], d["freq"], d["cross"],
                       d.get("exclude", []), d["feature_names"], d["raw_channels"] + [x["name"] for x in d["derived"]])


def build_spec(profile: dict, fcfg: dict) -> FeatureSpec:
    specs = channel_specs(profile)
    raw = [s["canonical"] for s in specs]
    meta = {s["canonical"]: {"position": s["position"], "signal": s["signal"], "axis": s["axis"]} for s in specs}
    positions = list(profile["sensor"]["channels"]["positions"])
    derived = []
    for d in fcfg.get("derived_channels", []):
        if d.get("type") != "magnitude":
            raise ValueError(f"unsupported derived channel type {d.get('type')!r}; supported: magnitude")
        for pos in positions:
            for sig in d["signals"]:
                members = [c for c in raw if meta[c]["position"] == pos and meta[c]["signal"] == sig]
                if not members:
                    raise ValueError(f"derived magnitude: no channels for {pos}/{sig}")
                name = f"{pos}_{sig}_mag"
                derived.append({"name": name, "kind": "magnitude", "members": members})
                meta[name] = {"position": pos, "signal": sig, "axis": "mag"}
    all_ch = raw + [d["name"] for d in derived]
    td = list(fcfg.get("time_domain", []))
    for t in td:
        if t not in TIME_FEATURES:
            raise ValueError(f"unsupported time-domain feature {t!r}")
    fq = dict(fcfg.get("frequency", {}))
    if fq.get("enabled"):
        chs = fq.get("channels", "all")
        fq["channel_list"] = all_ch if chs == "all" else list(chs)
        unknown = set(fq["channel_list"]) - set(all_ch)
        if unknown:
            raise ValueError(f"frequency.channels contains unknown channels {sorted(unknown)}")
    cross = list(fcfg.get("cross_sensor", []))
    for c in cross:
        if c.get("type") not in ("correlation", "std_log_ratio"):
            raise ValueError(f"unsupported cross-sensor feature type {c.get('type')!r}")
        if c["a"] not in all_ch or c["b"] not in all_ch:
            raise ValueError(f"cross-sensor feature {c.get('name')}: unknown channel {c['a']!r} or {c['b']!r}")
    names = []
    for ch in all_ch:
        names += [f"{ch}__{t}" for t in td]
    if fq.get("enabled"):
        for ch in fq["channel_list"]:
            for f in fq["features"]:
                if f == "dominant_freq": names.append(f"{ch}__dominant_freq")
                elif f == "band_energy": names += [f"{ch}__band_energy_{b}" for b in fq["bands_hz"]]
                elif f == "band_ratio": names += [f"{ch}__band_ratio_{a}_over_{b}" for a, b in fq.get("ratios", [])]
                elif f == "spectral_entropy": names.append(f"{ch}__spectral_entropy")
                elif f == "total_power": names.append(f"{ch}__total_power")
    names += [f"cross__{c['name']}" for c in cross]
    excl = list(fcfg.get("exclude_features", []))
    names = [n for n in names if not any(fnmatch.fnmatch(n, pat) for pat in excl)]
    return FeatureSpec(raw, positions, meta, derived, td, fq, cross, excl, names, all_ch)


def _with_derived(W: np.ndarray, spec: FeatureSpec) -> np.ndarray:
    """W: (N, n, n_raw) -> (N, n, n_all) with magnitude channels appended."""
    ix = {c: i for i, c in enumerate(spec.raw_channels)}
    extra = [np.sqrt(np.sum(W[:, :, [ix[m] for m in d["members"]]] ** 2, axis=2)) for d in spec.derived]
    return np.concatenate([W] + [e[:, :, None] for e in extra], axis=2) if extra else W


def window_features(W: np.ndarray, spec: FeatureSpec, fs: float) -> pd.DataFrame:
    """Compute all configured features for windows W of shape (N, n, len(spec.raw_channels)). Only W is used."""
    if W.ndim != 3 or W.shape[2] != len(spec.raw_channels):
        raise ValueError(f"expected (N, n, {len(spec.raw_channels)}) windows, got {W.shape}")
    X = _with_derived(W.astype(np.float64, copy=False), spec)
    N, n, C = X.shape
    cols: dict[str, np.ndarray] = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        td = spec.time_domain
        stats: dict[str, np.ndarray] = {}
        if "mean" in td: stats["mean"] = np.nanmean(X, axis=1)
        if "std" in td: stats["std"] = np.nanstd(X, axis=1)
        if "min" in td: stats["min"] = np.nanmin(X, axis=1)
        if "max" in td: stats["max"] = np.nanmax(X, axis=1)
        if "rms" in td: stats["rms"] = np.sqrt(np.nanmean(X ** 2, axis=1))
        if "range" in td: stats["range"] = np.nanmax(X, axis=1) - np.nanmin(X, axis=1)
        if "mean_abs_diff" in td: stats["mean_abs_diff"] = np.nanmean(np.abs(np.diff(X, axis=1)), axis=1)
        qs = [(k, q) for k, q in (("q25", 25), ("median", 50), ("q75", 75)) if k in td]
        if qs:
            qv = np.nanpercentile(X, [q for _, q in qs], axis=1)
            for (k, _), v in zip(qs, qv):
                stats[k] = v
        for ci, ch in enumerate(spec.all_channels):
            for t in td:
                cols[f"{ch}__{t}"] = stats[t][:, ci]
        fq = spec.freq
        if fq.get("enabled"):
            cols.update(_freq_features(X, spec, fs))
        for c in spec.cross:
            a = X[:, :, spec.all_channels.index(c["a"])]; b = X[:, :, spec.all_channels.index(c["b"])]
            if c["type"] == "correlation":
                am = a - np.nanmean(a, axis=1, keepdims=True); bm = b - np.nanmean(b, axis=1, keepdims=True)
                den = np.sqrt(np.nansum(am ** 2, axis=1) * np.nansum(bm ** 2, axis=1))
                v = np.where(den > EPS, np.nansum(am * bm, axis=1) / np.where(den > EPS, den, 1), np.nan)
            else:
                v = np.log10((np.nanstd(a, axis=1) + EPS) / (np.nanstd(b, axis=1) + EPS))
            cols[f"cross__{c['name']}"] = v
    df = pd.DataFrame({k: cols[k] for k in spec.names}).astype(np.float32)
    return df


def _freq_features(X: np.ndarray, spec: FeatureSpec, fs: float) -> dict[str, np.ndarray]:
    fq = spec.freq
    N, n, _ = X.shape
    idx = [spec.all_channels.index(c) for c in fq["channel_list"]]
    S = X[:, :, idx]
    bad = np.isnan(S).any(axis=1)                              # (N, Cf): spectra need gap-free windows
    S = np.where(np.isnan(S), 0.0, S)
    if fq.get("detrend", "mean") == "mean":
        S = S - S.mean(axis=1, keepdims=True)
    if fq.get("window_function", "hann") == "hann":
        S = S * np.hanning(n)[None, :, None]
    P = (np.abs(np.fft.rfft(S, axis=1)) ** 2) / n              # (N, nf, Cf)
    f = np.fft.rfftfreq(n, d=1.0 / fs)
    lo, hi = fq.get("dominant_range_hz", [0.5, fs / 2])
    hi = min(hi, fs / 2)
    rng = (f >= lo) & (f <= hi)
    out: dict[str, np.ndarray] = {}
    logE = bool(fq.get("log_energy", True))
    tf = (lambda e: np.log10(1.0 + e)) if logE else (lambda e: e)
    bands = {k: (f >= v[0]) & (f < v[1]) for k, v in fq["bands_hz"].items()}
    be = {k: P[:, m, :].sum(axis=1) for k, m in bands.items()}
    for ci, ch in enumerate(fq["channel_list"]):
        nanmask = bad[:, ci]
        def put(name, v):
            v = np.array(v, dtype=np.float64); v[nanmask] = np.nan
            out[f"{ch}__{name}"] = v
        for feat in fq["features"]:
            if feat == "dominant_freq":
                pr = np.where(rng[None, :], P[:, :, ci], -1.0)
                put("dominant_freq", f[np.argmax(pr, axis=1)])
            elif feat == "band_energy":
                for b in fq["bands_hz"]: put(f"band_energy_{b}", tf(be[b][:, ci]))
            elif feat == "band_ratio":
                for a, b in fq.get("ratios", []):
                    put(f"band_ratio_{a}_over_{b}", np.log10((be[a][:, ci] + EPS) / (be[b][:, ci] + EPS)))
            elif feat == "spectral_entropy":
                pw = P[:, rng, ci]; tot = pw.sum(axis=1, keepdims=True)
                p = pw / np.where(tot > EPS, tot, 1.0)
                ent = -(p * np.log(p + EPS)).sum(axis=1) / np.log(max(2, rng.sum()))
                put("spectral_entropy", np.where(tot[:, 0] > EPS, ent, np.nan))
            elif feat == "total_power":
                put("total_power", tf(P[:, rng, ci].sum(axis=1)))
    return out


def causal_fill(sig: np.ndarray, max_ffill: int) -> np.ndarray:
    """Forward-fill isolated NaN runs of at most `max_ffill` samples (uses past values only)."""
    if max_ffill <= 0:
        return sig
    df = pd.DataFrame(sig)
    return df.ffill(limit=max_ffill).to_numpy()


def windows_from_signal(sig: np.ndarray, end_idx: np.ndarray, n: int) -> np.ndarray:
    """(T, C) signal and window end indices (inclusive) -> (N, n, C) windows ending at those indices."""
    from numpy.lib.stride_tricks import sliding_window_view
    sw = sliding_window_view(sig, n, axis=0)             # (T-n+1, C, n)
    return np.ascontiguousarray(np.transpose(sw[end_idx - n + 1], (0, 2, 1)))


def position_quality(raw_sig: np.ndarray, filled: np.ndarray, end_idx: np.ndarray, n: int, spec: FeatureSpec,
                     min_valid_fraction: float) -> dict[str, np.ndarray]:
    """Per sensor position: raw valid fraction of the weakest channel >= threshold AND no NaN left after filling."""
    out = {}
    ok_raw = np.cumsum(np.vstack([np.zeros((1, raw_sig.shape[1])), ~np.isnan(raw_sig)]), axis=0)
    ok_fill = np.cumsum(np.vstack([np.zeros((1, filled.shape[1])), ~np.isnan(filled)]), axis=0)
    lo = end_idx - n + 1
    raw_frac = (ok_raw[end_idx + 1] - ok_raw[lo]) / n
    fill_frac = (ok_fill[end_idx + 1] - ok_fill[lo]) / n
    for pos in spec.positions:
        ci = [i for i, c in enumerate(spec.raw_channels) if spec.channel_meta[c]["position"] == pos]
        out[pos] = (raw_frac[:, ci].min(axis=1) >= min_valid_fraction) & (fill_frac[:, ci].min(axis=1) >= 1.0 - 1e-12)
    return out
