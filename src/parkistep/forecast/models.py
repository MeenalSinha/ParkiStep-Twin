"""Datasets, leakage-safe preprocessing + model pipelines, participant folds, weights, grouped training."""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, ClassifierMixin
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from .alerts import select_threshold
from .features import FeatureSpec
from .windows import horizon_key

log = logging.getLogger(__name__)
CLIN_PREFIX = "clin__"


class InsufficientClassesError(ValueError):
    pass


class RandomScoreClassifier(BaseEstimator, ClassifierMixin):
    """Reference model: uniformly random scores (chance-level alerting at any threshold)."""

    def __init__(self, random_state=0):
        self.random_state = random_state

    def fit(self, X, y, sample_weight=None):
        self.classes_ = np.unique(y)
        return self

    def predict_proba(self, X):
        u = np.random.RandomState(self.random_state).uniform(size=len(X))
        return np.c_[1 - u, u]

    def predict(self, X):
        return (self.predict_proba(X)[:, 1] >= 0.5).astype(int)


def make_estimator(spec: dict, seed: int):
    t, params = spec["type"], dict(spec.get("params", {}))
    if t == "dummy_prior":
        return DummyClassifier(strategy="prior")
    if t == "dummy_random":
        return RandomScoreClassifier(random_state=seed)
    if t == "logistic_regression":
        return LogisticRegression(random_state=seed, **params)
    if t == "random_forest":
        return RandomForestClassifier(random_state=seed, **params)
    raise ValueError(f"unsupported model type {t!r}")


def feature_positions(name: str, fspec: FeatureSpec) -> set[str]:
    if name.startswith("cross__"):
        cname = name[len("cross__"):]
        c = next(c for c in fspec.cross if c["name"] == cname)
        return {fspec.channel_meta[c["a"]]["position"], fspec.channel_meta[c["b"]]["position"]}
    return {fspec.position_of(name)}


def sensor_columns(fspec: FeatureSpec, positions: list[str]) -> list[str]:
    return [n for n in fspec.names if feature_positions(n, fspec) <= set(positions)]


def clinical_columns(cfg: dict) -> tuple[list[str], list[str]]:
    feats = list(cfg["data"].get("clinical_features", []))
    cat = set(cfg["data"].get("clinical_categorical", []))
    return [CLIN_PREFIX + f for f in feats if f not in cat], [CLIN_PREFIX + f for f in feats if f in cat]


def assemble(anchors: pd.DataFrame, features: pd.DataFrame, clinical: pd.DataFrame, cfg: dict, fspec: FeatureSpec,
             horizon: float, exp: dict, clinical_key: str) -> tuple[pd.DataFrame, dict]:
    """Eligible rows for one horizon + experiment. Returns the frame and a column-group description."""
    hk = horizon_key(horizon)
    positions = cfg["data"]["sensor_sets"][exp["sensor_set"]]
    m = anchors[anchors[f"{hk}_status"] == "eligible"]
    for p in positions:
        m = m[m[f"qc_{p}"]]
    scols = sensor_columns(fspec, positions)
    df = m[["window_id", "recording_id", "participant_id", "session_id", "task_id", "anchor_idx", "anchor_t", "dt_s",
            f"{hk}_y", f"{hk}_event_id", f"{hk}_time_to_onset"]].rename(
        columns={f"{hk}_y": "y", f"{hk}_event_id": "event_id", f"{hk}_time_to_onset": "time_to_onset"})
    df = df.merge(features[["window_id"] + scols], on="window_id", how="left", validate="one_to_one")
    cnum, ccat = clinical_columns(cfg)
    clin_cols: list[str] = []
    if exp.get("clinical"):
        cl = clinical[[clinical_key] + [c[len(CLIN_PREFIX):] for c in cnum + ccat]].copy()
        cl.columns = ["participant_id"] + cnum + ccat
        before = len(df)
        df = df.merge(cl, on="participant_id", how="left", validate="many_to_one")
        assert len(df) == before, "clinical join changed the number of windows"
        clin_cols = cnum + ccat
    step = np.maximum(1, np.round(cfg["windowing"]["stride_s"] / df["dt_s"]).astype(int))
    df["stride_ord"] = (df["anchor_idx"] // step).astype(int)
    return df.reset_index(drop=True), {"sensor": scols, "clin_num": cnum if exp.get("clinical") else [],
                                       "clin_cat": ccat if exp.get("clinical") else []}


def build_pipeline(groups: dict, model_spec: dict, prep: dict, seed: int) -> Pipeline:
    sc = [("imp", SimpleImputer(strategy=prep.get("sensor_impute", "median"), keep_empty_features=True))]
    if prep.get("scale", True):
        sc.append(("scale", StandardScaler()))
    parts = [("sensor", Pipeline(sc), groups["sensor"])]
    if groups["clin_num"]:
        cn = [("imp", SimpleImputer(strategy=prep.get("clinical_impute", "median"), keep_empty_features=True))]
        if prep.get("scale", True):
            cn.append(("scale", StandardScaler()))
        parts.append(("clin_num", Pipeline(cn), groups["clin_num"]))
    if groups["clin_cat"]:
        parts.append(("clin_cat", Pipeline([("imp", SimpleImputer(strategy="most_frequent", keep_empty_features=True)),
                                            ("oh", OneHotEncoder(handle_unknown="ignore"))]), groups["clin_cat"]))
    ct = ColumnTransformer(parts, remainder="drop", verbose_feature_names_out=False)
    return Pipeline([("prep", ct), ("model", make_estimator(model_spec, seed))])


def subject_balanced_weights(participants: pd.Series) -> np.ndarray:
    n = participants.map(participants.value_counts())
    w = 1.0 / n.to_numpy(dtype=float)
    return w * (len(w) / w.sum())


def fit_pipeline(df: pd.DataFrame, groups: dict, model_spec: dict, cfg: dict, seed: int) -> Pipeline:
    t = cfg["training"]
    d = df[df["stride_ord"] % int(t.get("train_stride_multiplier", 1)) == 0]
    y = d["y"].to_numpy()
    if len(np.unique(y)) < 2:
        raise InsufficientClassesError(f"training data has a single class ({np.unique(y).tolist()}); cannot fit a classifier")
    cols = groups["sensor"] + groups["clin_num"] + groups["clin_cat"]
    pipe = build_pipeline(groups, model_spec, cfg["preprocessing"], seed)
    kw = {}
    if t.get("subject_balanced_weights", True):
        kw["model__sample_weight"] = subject_balanced_weights(d["participant_id"])
    pipe.fit(d[cols], y, **kw)
    return pipe


def predict_proba(pipe: Pipeline, df: pd.DataFrame, groups: dict) -> np.ndarray:
    cols = groups["sensor"] + groups["clin_num"] + groups["clin_cat"]
    p = pipe.predict_proba(df[cols])
    cls = list(pipe.classes_)
    out = p[:, cls.index(1)] if 1 in cls else np.zeros(len(df))
    if not np.all(np.isfinite(out)) or out.min() < -1e-9 or out.max() > 1 + 1e-9:
        raise ValueError("model produced invalid probabilities")
    return np.clip(out, 0.0, 1.0)


def assign_group_folds(stats: pd.DataFrame, n_splits: int, seed: int, strategy: str = "balanced_group_kfold") -> dict:
    """participant -> fold. `stats` has columns participant_id and weight (e.g. number of events). Deterministic.
    Participants are placed greedily (largest weight first) into the currently lightest fold."""
    ids = stats["participant_id"].tolist()
    if strategy == "leave_one_group_out":
        return {p: i for i, p in enumerate(sorted(ids))}
    if n_splits < 2 or n_splits > len(ids):
        raise ValueError(f"n_splits={n_splits} impossible with {len(ids)} participants")
    rng = np.random.RandomState(seed)
    tie = dict(zip(ids, rng.permutation(len(ids))))
    order = sorted(ids, key=lambda p: (-float(stats.set_index("participant_id").loc[p, "weight"]), tie[p]))
    load = np.zeros(n_splits); count = np.zeros(n_splits)
    out = {}
    w = stats.set_index("participant_id")["weight"]
    for p in order:
        k = int(np.lexsort((count, load))[0])
        out[p] = k; load[k] += float(w.loc[p]) + 1e-6; count[k] += 1
    return out


def inner_oof_threshold(train: pd.DataFrame, groups: dict, model_spec: dict, cfg: dict, events: pd.DataFrame,
                        H: float, seed: int) -> dict:
    """Operating threshold chosen ONLY from grouped out-of-fold predictions inside the training participants."""
    tcfg = cfg["threshold"]
    if tcfg["policy"] == "fixed":
        return {"policy": "fixed", "threshold": float(tcfg["fixed_value"])}
    pw = train.groupby("participant_id")["y"].sum().rename("weight").reset_index()
    k = min(int(tcfg["inner_splits"]), len(pw))
    folds = assign_group_folds(pw, k, seed)
    oof = np.full(len(train), np.nan)
    fid = train["participant_id"].map(folds).to_numpy()
    for f in range(k):
        tr, va = train[fid != f], train[fid == f]
        if len(va) == 0:
            continue
        try:
            pipe = fit_pipeline(tr, groups, model_spec, cfg, seed + 1000 + f)
        except InsufficientClassesError:
            log.warning("inner fold %d has a single class; skipped for threshold selection", f)
            continue
        oof[(fid == f)] = predict_proba(pipe, va, groups)
    ok = ~np.isnan(oof)
    if not ok.any():
        raise InsufficientClassesError("no inner fold could be fitted for threshold selection")
    pred = train.loc[ok, ["recording_id", "participant_id", "anchor_t", "y", "event_id"]].copy()
    pred["prob"] = oof[ok]
    ev = events[events["participant_id"].isin(pred["participant_id"].unique())]
    out = select_threshold(pred, ev, H, float(cfg["windowing"]["stride_s"]), cfg["alerts"], tcfg)
    out["n_inner_rows"] = int(ok.sum())
    return out
