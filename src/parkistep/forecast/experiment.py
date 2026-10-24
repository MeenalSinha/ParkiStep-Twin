"""Phase 2 orchestration: prepare -> train (participant-grouped CV + final models) -> evaluate (metrics + reports)."""
from __future__ import annotations

import hashlib
import json
import logging
import platform
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from .features import build_spec
from .models import (InsufficientClassesError, assemble, assign_group_folds, fit_pipeline, inner_oof_threshold,
                     predict_proba)
from .prepare import Phase2Error, dataset_columns, run_prepare
from .settings import Phase2Settings
from .windows import horizon_key

log = logging.getLogger(__name__)


def prepare(s: Phase2Settings, force: bool = False) -> Path:
    return run_prepare(s, force=force)


def software_versions() -> dict:
    import sklearn, scipy
    return {"python": platform.python_version(), "platform": platform.platform(), "numpy": np.__version__,
            "pandas": pd.__version__, "scikit-learn": sklearn.__version__, "scipy": scipy.__version__, "joblib": joblib.__version__}


def load_cache(s: Phase2Settings) -> dict:
    d = s.cache_dir
    need = ["events.parquet", "anchors.parquet", "features.parquet", "window_summary.json"]
    miss = [n for n in need if not (d / n).exists()]
    if miss:
        raise Phase2Error(f"Window cache incomplete at {d} (missing {miss}). Run scripts/prepare_phase2_windows.py first.")
    out = {"events": pd.read_parquet(d / "events.parquet"), "anchors": pd.read_parquet(d / "anchors.parquet"),
           "features": pd.read_parquet(d / "features.parquet"), "summary": json.loads((d / "window_summary.json").read_text())}
    cp = d / "clinical.parquet"
    out["clinical"] = pd.read_parquet(cp) if cp.exists() else None
    return out


def make_folds(s: Phase2Settings, anchors: pd.DataFrame, events: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    parts = anchors.loc[anchors["base_status"] == "eligible_base", "participant_id"].unique()
    ne = events.groupby("participant_id").size()
    stats = pd.DataFrame({"participant_id": sorted(parts)})
    stats["weight"] = stats["participant_id"].map(ne).fillna(0).astype(float)
    sp = s.cfg["split"]
    folds = assign_group_folds(stats, int(sp["n_splits"]), int(sp["seed"]), sp["strategy"])
    stats["fold"] = stats["participant_id"].map(folds)
    return folds, stats


def _combo_name(exp, model, hk):
    return f"{exp}__{model}__{hk}"


def train(s: Phase2Settings, force: bool = False) -> Path:
    cfg = s.cfg
    out = s.artifact_dir
    (out / "oof").mkdir(parents=True, exist_ok=True)
    (out / "models").mkdir(exist_ok=True)
    fh = logging.FileHandler(out / "train.log"); fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(fh)
    try:
        c = load_cache(s)
        if c["clinical"] is None and any(e.get("clinical") for e in cfg["experiments"]):
            raise Phase2Error("clinical table missing from the window cache; rebuild with --force")
        cols = dataset_columns(s)
        fspec = build_spec(s.base.dataset, cfg["features"])
        anchors, feats, events, clin = c["anchors"], c["features"], c["events"], c["clinical"]
        folds, split_stats = make_folds(s, anchors, events)
        (out / "config_snapshot.json").write_text(json.dumps(s.snapshot(), indent=2, default=str))
        (out / "software_versions.json").write_text(json.dumps(software_versions(), indent=2))
        split_stats.to_csv(out / "split_manifest.csv", index=False)
        dm = s.base.processed_dir / s.base.project["outputs"]["manifest"]
        (out / "dataset_provenance.json").write_text(json.dumps({
            "dataset": s.base.dataset.get("name"), "version": s.base.dataset.get("version"), "source": s.base.dataset["source"],
            "license": s.base.dataset["license"], "attribution": s.base.dataset["attribution"],
            "phase1_manifest": json.loads(dm.read_text()).get("raw_files") if dm.exists() else None}, indent=2, default=str))
        failures, thr_log = [], {}
        seed0 = int(cfg["training"]["random_seed"])
        for H in [float(h) for h in cfg["windowing"]["horizons_s"]]:
            hk = horizon_key(H)
            for exp in cfg["experiments"]:
                df, groups = assemble(anchors, feats, clin, cfg, fspec, H, exp, cols["clinical_key"])
                df["fold"] = df["participant_id"].map(folds)
                if df["fold"].isna().any():
                    raise Phase2Error("some windows belong to participants without a fold assignment")
                for mname, mspec in cfg["models"].items():
                    name = _combo_name(exp["name"], mname, hk)
                    fp = out / "oof" / f"{name}.parquet"
                    if fp.exists() and not force:
                        log.info("skip CV for %s (predictions exist)", name)
                        if cfg["training"].get("final_models", True) and not (out / "models" / f"{name}.joblib").exists():
                            _fit_final(s, df, groups, exp, mname, mspec, H, events, fspec, out, name)
                        continue
                    t0 = time.time()
                    try:
                        recs, tinfo = [], {}
                        for f in sorted(df["fold"].unique()):
                            tr, te = df[df["fold"] != f], df[df["fold"] == f]
                            assert set(tr["participant_id"]).isdisjoint(set(te["participant_id"]))
                            pipe = fit_pipeline(tr, groups, mspec, cfg, seed0 + int(f))
                            ti = inner_oof_threshold(tr, groups, mspec, cfg, events, H, seed0 + 31 * int(f))
                            p = predict_proba(pipe, te, groups)
                            r = te[["window_id", "recording_id", "participant_id", "anchor_t", "y", "event_id", "time_to_onset", "fold"]].copy()
                            r["prob"] = p; r["threshold"] = ti["threshold"]
                            recs.append(r); tinfo[int(f)] = ti
                        res = pd.concat(recs, ignore_index=True)
                        res.insert(0, "horizon_s", H); res.insert(0, "model", mname); res.insert(0, "experiment", exp["name"])
                        res.to_parquet(fp, index=False)
                        thr_log[name] = tinfo
                        (out / "oof" / f"{name}.thresholds.json").write_text(json.dumps(tinfo, indent=2, default=str))
                        log.info("CV %s done in %.0fs (%d windows)", name, time.time() - t0, len(res))
                    except InsufficientClassesError as e:
                        failures.append({"combo": name, "error": str(e)}); log.error("CV %s failed: %s", name, e)
                        continue
                    if cfg["training"].get("final_models", True):
                        _fit_final(s, df, groups, exp, mname, mspec, H, events, fspec, out, name)
        (out / "failures.json").write_text(json.dumps(failures, indent=2))
        log.info("Training finished; artifacts in %s", out)
        return out
    finally:
        logging.getLogger().removeHandler(fh); fh.close()


def _fit_final(s, df, groups, exp, mname, mspec, H, events, fspec, out, name):
    cfg = s.cfg
    seed = int(cfg["training"]["random_seed"])
    pipe = fit_pipeline(df, groups, mspec, cfg, seed + 7)
    ti = inner_oof_threshold(df, groups, mspec, cfg, events, H, seed + 997)
    path = out / "models" / f"{name}.joblib"
    joblib.dump(pipe, path)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    cat_levels = {}
    if groups["clin_cat"]:
        oh = pipe.named_steps["prep"].named_transformers_["clin_cat"].named_steps["oh"]
        cat_levels = {c: [str(x) for x in lv] for c, lv in zip(groups["clin_cat"], oh.categories_)}
    W = cfg["windowing"]
    manifest = {
        "artifact": path.name, "sha256": sha, "experiment_id": s.experiment_id, "experiment": exp["name"], "model": mname,
        "model_spec": mspec, "horizon_s": H, "window": {"input_window_s": W["input_window_s"], "stride_s": W["stride_s"],
                                                        "timestamp_tolerance_fraction": W["timestamp_tolerance_fraction"],
                                                        "min_valid_fraction": W["min_valid_fraction"], "max_ffill_samples": W["max_ffill_samples"]},
        "sensor_positions": cfg["data"]["sensor_sets"][exp["sensor_set"]], "uses_clinical": bool(exp.get("clinical")),
        "feature_manifest": {"sensor_features": groups["sensor"], "clinical_numeric": groups["clin_num"],
                             "clinical_categorical": groups["clin_cat"], "categorical_levels": cat_levels,
                             "raw_channels": fspec.raw_channels},
        "feature_spec": fspec.to_dict(), "channel_meta": {c: fspec.channel_meta[c] for c in fspec.raw_channels}, "threshold": ti, "alerts": cfg["alerts"],
        "training": {"n_windows": int(len(df)), "n_participants": int(df["participant_id"].nunique()),
                     "n_positive": int(df["y"].sum()), "median_dt_s": float(df["dt_s"].median()), "fitted_on": "all participants (final model; not for evaluation)"},
        "clinical_source_fields": {c[len('clin__'):]: ("categorical" if c in groups["clin_cat"] else "numeric") for c in groups["clin_num"] + groups["clin_cat"]},
        "config_hash": s.config_hash, "software": software_versions(),
        "intended_use": "research proof of concept; the output is a model score, not a clinical diagnosis"}
    (out / "models" / f"{name}.manifest.json").write_text(json.dumps(manifest, indent=2, default=str))


def evaluate(s: Phase2Settings):
    from .evaluate import run_evaluate
    return run_evaluate(s)
