import inspect

import numpy as np
import pandas as pd
import pytest

from parkistep.config import load_settings
from parkistep.forecast import features as F
from parkistep.forecast.settings import load_phase2
from p2helpers import REPO


@pytest.fixture(scope="module")
def s():
    return load_phase2(REPO / "configs/phase2.yaml", REPO)


@pytest.fixture(scope="module")
def spec(s):
    return F.build_spec(s.base.dataset, s.cfg["features"])


def _win(spec, N=4, n=120, seed=0):
    return np.random.default_rng(seed).normal(size=(N, n, len(spec.raw_channels)))


def test_names_deterministic_and_unique(s, spec):
    spec2 = F.build_spec(s.base.dataset, s.cfg["features"])
    assert spec.names == spec2.names and len(set(spec.names)) == len(spec.names)
    X = F.window_features(_win(spec), spec, fs=60.0)
    assert list(X.columns) == spec.names and X.shape == (4, len(spec.names))
    assert X.equals(F.window_features(_win(spec), spec, fs=60.0))


def test_features_do_not_depend_on_anything_but_the_window(spec):
    W = _win(spec)
    a = F.window_features(W, spec, 60.0)
    W2 = W.copy()
    assert a.equals(F.window_features(W2, spec, 60.0))
    sig = inspect.signature(F.window_features)
    assert list(sig.parameters) == ["W", "spec", "fs"]                      # no annotation / future-data argument exists


def test_causal_window_extraction_uses_only_past_samples(spec):
    sig = np.random.default_rng(1).normal(size=(500, len(spec.raw_channels)))
    n = 120
    base = F.window_features(F.windows_from_signal(sig, np.array([300]), n), spec, 60.0)
    sig2 = sig.copy(); sig2[301:] = 1e6                                     # corrupt the FUTURE
    assert base.equals(F.window_features(F.windows_from_signal(sig2, np.array([300]), n), spec, 60.0))
    causal = F.causal_fill(np.array([[1.0], [np.nan], [np.nan], [np.nan], [np.nan], [5.0]]), 2)
    assert causal[1, 0] == 1.0 and causal[2, 0] == 1.0 and np.isnan(causal[3, 0])      # fills forward only, bounded; never uses 5.0


def test_known_values_and_dominant_frequency():
    prof = load_settings(REPO / "configs/datasets.yaml").dataset
    fc = {"time_domain": ["mean", "std", "min", "max", "rms", "range"], "derived_channels": [],
          "frequency": {"enabled": True, "channels": ["back_acc_x"], "window_function": "none", "detrend": "mean",
                        "dominant_range_hz": [0.5, 15.0], "bands_hz": {"lo": [0.5, 3.0], "hi": [3.0, 8.0]}, "ratios": [["hi", "lo"]],
                        "features": ["dominant_freq", "band_energy", "band_ratio", "spectral_entropy", "total_power"], "log_energy": True},
          "cross_sensor": []}
    sp = F.build_spec(prof, fc)
    fs, n = 60.0, 120
    t = np.arange(n) / fs
    W = np.zeros((1, n, len(sp.raw_channels)))
    W[0, :, sp.raw_channels.index("back_acc_x")] = 2.0 + np.sin(2 * np.pi * 4.0 * t)
    X = F.window_features(W, sp, fs)
    assert X["back_acc_x__mean"].iloc[0] == pytest.approx(2.0, abs=1e-3)
    assert X["back_acc_x__max"].iloc[0] == pytest.approx(3.0, abs=1e-2) and X["back_acc_x__rms"].iloc[0] > 2.0
    assert X["back_acc_x__dominant_freq"].iloc[0] == pytest.approx(4.0, abs=0.5)
    assert X["back_acc_x__band_ratio_hi_over_lo"].iloc[0] > 1.0              # energy sits in the 3-8 Hz band


def test_unsupported_features_fail_clearly(s):
    prof = s.base.dataset
    with pytest.raises(ValueError, match="unsupported time-domain"):
        F.build_spec(prof, {**s.cfg["features"], "time_domain": ["skewness"]})
    with pytest.raises(ValueError, match="unsupported derived"):
        F.build_spec(prof, {**s.cfg["features"], "derived_channels": [{"type": "jerk", "signals": ["acc"]}]})
    with pytest.raises(ValueError, match="unknown channel"):
        F.build_spec(prof, {**s.cfg["features"], "cross_sensor": [{"name": "x", "type": "correlation", "a": "nope", "b": "back_acc_x"}]})


def test_feature_names_never_contain_annotations_or_identifiers(spec):
    bad = ("fog", "severity", "participant", "subject", "session", "task", "activity", "timestamp", "event")
    assert not [n for n in spec.names if any(b in n.lower() for b in bad)]


def test_missing_channel_behaviour_nan_features_not_crash(spec):
    W = _win(spec, N=2)
    W[0, :, 0] = np.nan                                                      # whole channel missing in window 0
    X = F.window_features(W, spec, 60.0)
    assert X.iloc[0][[n for n in spec.names if n.startswith(spec.raw_channels[0] + "__")]].isna().all()
    assert X.iloc[1].notna().all()


def test_exclude_features_pattern(s):
    spec = F.build_spec(s.base.dataset, {**s.cfg["features"], "exclude_features": ["*__q25", "*total_power"]})
    assert not any(n.endswith("__q25") or n.endswith("total_power") for n in spec.names)
