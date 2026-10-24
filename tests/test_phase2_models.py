import numpy as np
import pandas as pd
import pytest

from parkistep.forecast import models as M
from parkistep.forecast.features import build_spec
from parkistep.forecast.settings import load_phase2
from p2helpers import REPO

S = load_phase2(REPO / "configs/phase2.yaml", REPO)
CFG = S.cfg


def _groups(n_sensor=6, clin=True):
    return {"sensor": [f"s{i}" for i in range(n_sensor)], "clin_num": ["clin__age", "clin__moca"] if clin else [],
            "clin_cat": ["clin__gender"] if clin else []}


def _df(n_part=10, per=60, seed=0, signal=True):
    rng = np.random.RandomState(seed)
    rows = []
    for p in range(1, n_part + 1):
        y = (rng.uniform(size=per) < 0.25).astype(int)
        d = pd.DataFrame({"participant_id": p, "recording_id": f"r{p}", "anchor_t": np.arange(per) * 0.2, "y": y,
                          "event_id": np.where(y == 1, f"r{p}-e0", ""), "stride_ord": np.arange(per), "dt_s": 0.1})
        for i in range(6):
            d[f"s{i}"] = rng.normal(size=per) + (1.5 * y if (signal and i < 2) else 0)
        d["clin__age"] = 60 + p; d["clin__moca"] = 20.0 + (p % 3); d["clin__gender"] = "M" if p % 2 else "F"
        rows.append(d)
    return pd.concat(rows, ignore_index=True)


@pytest.mark.parametrize("name", list(CFG["models"]))
def test_every_configured_model_trains_and_gives_valid_probabilities(name):
    df = _df(); spec = CFG["models"][name]
    pipe = M.fit_pipeline(df, _groups(), spec, CFG, seed=1)
    p = M.predict_proba(pipe, df, _groups())
    assert p.shape == (len(df),) and np.isfinite(p).all() and p.min() >= 0 and p.max() <= 1
    if spec["type"] in ("logistic_regression", "random_forest"):
        from sklearn.metrics import roc_auc_score
        assert roc_auc_score(df.y, p) > 0.7


def test_training_with_one_class_fails_clearly():
    df = _df(); df["y"] = 0
    with pytest.raises(M.InsufficientClassesError):
        M.fit_pipeline(df, _groups(), CFG["models"]["logistic"], CFG, 1)


def test_folds_are_participant_disjoint_balanced_and_deterministic():
    stats = pd.DataFrame({"participant_id": range(1, 23), "weight": [5, 0, 3, 9, 1, 0, 12, 4, 4, 2, 0, 7, 3, 3, 8, 1, 2, 6, 0, 5, 4, 1]})
    f1 = M.assign_group_folds(stats, 5, 7); f2 = M.assign_group_folds(stats, 5, 7)
    assert f1 == f2 and set(f1) == set(range(1, 23)) and set(f1.values()) == set(range(5))
    load = stats.assign(f=stats.participant_id.map(f1)).groupby("f").weight.sum()
    assert load.max() - load.min() <= 12
    assert len(set(M.assign_group_folds(stats, 5, 7, "leave_one_group_out").values())) == 22
    with pytest.raises(ValueError):
        M.assign_group_folds(stats.head(3), 5, 1)


def test_scaler_and_imputer_are_fitted_on_training_rows_only():
    df = _df(); test = df[df.participant_id == 10].copy(); train = df[df.participant_id != 10].copy()
    test["s0"] = 1e6; test["clin__moca"] = np.nan
    pipe = M.fit_pipeline(train, _groups(), CFG["models"]["logistic"], CFG, 1)
    sc = pipe.named_steps["prep"].named_transformers_["sensor"].named_steps["scale"]
    d = train[train.stride_ord % CFG["training"]["train_stride_multiplier"] == 0]
    assert sc.mean_[0] == pytest.approx(d["s0"].mean()) and abs(sc.mean_[0]) < 10                       # test outlier did not leak in
    imp = pipe.named_steps["prep"].named_transformers_["clin_num"].named_steps["imp"]
    assert imp.statistics_[1] == pytest.approx(d["clin__moca"].median())
    M.predict_proba(pipe, test, _groups())                                                              # NaN clinical value handled by policy


def test_subject_balanced_weights_equalise_participants():
    p = pd.Series([1] * 10 + [2] * 30)
    w = M.subject_balanced_weights(p)
    assert w[p == 1].sum() == pytest.approx(w[p == 2].sum()) and w.mean() == pytest.approx(1.0)


def test_sensor_only_pipeline_has_no_clinical_columns_and_fusion_has_both():
    df = _df()
    so = M.fit_pipeline(df, _groups(clin=False), CFG["models"]["logistic"], CFG, 1)
    fu = M.fit_pipeline(df, _groups(), CFG["models"]["logistic"], CFG, 1)
    assert not [c for c in so.named_steps["prep"].get_feature_names_out() if c.startswith("clin__")]
    names = list(fu.named_steps["prep"].get_feature_names_out())
    assert {"s0", "clin__age", "clin__moca"} <= set(names) and any(n.startswith("clin__gender") for n in names)
    for forbidden in ("participant_id", "recording_id", "anchor_t", "y", "event_id"):
        assert forbidden not in names


def test_assemble_joins_clinical_by_participant_and_keeps_row_count():
    spec = build_spec(S.base.dataset, CFG["features"])
    ids = [1, 1, 2]
    anchors = pd.DataFrame({"window_id": [0, 1, 2], "recording_id": ["a", "a", "b"], "participant_id": ids, "session_id": 1, "task_id": 1,
                            "anchor_idx": [10, 12, 10], "anchor_t": [1.0, 1.2, 1.0], "dt_s": 0.1, "h1_status": "eligible", "h1_y": [0, 1, 0],
                            "h1_event_id": ["", "a-e0", ""], "h1_time_to_onset": [np.nan, .5, np.nan]})
    for p in spec.positions:
        anchors[f"qc_{p}"] = True
    names = M.sensor_columns(spec, spec.positions)
    feats = pd.DataFrame(np.random.rand(3, len(names)), columns=names); feats.insert(0, "window_id", [0, 1, 2])
    clin = pd.DataFrame({"participant_id": [1, 2], "age": [60.0, 80.0], "gender": ["M", "F"], "disease_duration": [5.0, 9.0],
                         "hoehn_yahr": [2.0, 3.0], "updrs_iii": [20.0, 40.0], "fog_q": [10.0, 20.0], "moca": [25.0, np.nan], "pdq_8": [3.0, 8.0]})
    df, g = M.assemble(anchors, feats, clin, CFG, spec, 1.0, {"name": "fusion", "sensor_set": "all", "clinical": True}, "participant_id")
    assert len(df) == 3 and df.loc[df.participant_id == 1, "clin__age"].eq(60.0).all() and df.loc[df.participant_id == 2, "clin__age"].eq(80.0).all()
    assert g["clin_cat"] == ["clin__gender"] and "clin__participant_id" not in df.columns
    so, gs = M.assemble(anchors, feats, clin, CFG, spec, 1.0, {"name": "sensor_only", "sensor_set": "all", "clinical": False}, "participant_id")
    assert not [c for c in so.columns if c.startswith("clin__")] and gs["clin_num"] == []


def test_assemble_drops_windows_failing_sensor_quality_for_the_sensor_set():
    spec = build_spec(S.base.dataset, CFG["features"])
    anchors = pd.DataFrame({"window_id": [0, 1], "recording_id": ["a", "a"], "participant_id": 1, "session_id": 1, "task_id": 1,
                            "anchor_idx": [10, 12], "anchor_t": [1.0, 1.2], "dt_s": 0.1, "h1_status": "eligible", "h1_y": [0, 0],
                            "h1_event_id": ["", ""], "h1_time_to_onset": np.nan})
    for p in spec.positions:
        anchors[f"qc_{p}"] = [True, p != "wrist"]
    names = M.sensor_columns(spec, spec.positions)
    feats = pd.DataFrame(np.zeros((2, len(names))), columns=names); feats.insert(0, "window_id", [0, 1])
    df, g = M.assemble(anchors, feats, pd.DataFrame(), CFG, spec, 1.0, {"name": "x", "sensor_set": "all", "clinical": False}, "participant_id")
    assert df.window_id.tolist() == [0]
    # a position subset that excludes wrist keeps both windows and drops wrist-dependent features
    CFG2 = {**CFG, "data": {**CFG["data"], "sensor_sets": {**CFG["data"]["sensor_sets"], "no_wrist": ["ankleL", "ankleR", "back"]}}}
    df2, g2 = M.assemble(anchors, feats, pd.DataFrame(), CFG2, spec, 1.0, {"name": "x", "sensor_set": "no_wrist", "clinical": False}, "participant_id")
    assert len(df2) == 2 and not any(c.startswith("wrist_") for c in g2["sensor"])


def test_inner_threshold_selection_sees_only_training_participants():
    df = _df(n_part=9); events = pd.DataFrame({"event_id": [f"r{p}-e0" for p in range(1, 10)], "recording_id": [f"r{p}" for p in range(1, 10)],
                                                "participant_id": range(1, 10), "onset_t": 5.0})
    train = df[df.participant_id <= 6]
    t1 = M.inner_oof_threshold(train, _groups(clin=False), CFG["models"]["logistic"], CFG, events, 3.0, 5)
    assert t1["n_inner_rows"] <= len(train) and t1["policy"] == CFG["threshold"]["policy"]
    t2 = M.inner_oof_threshold(train, _groups(clin=False), CFG["models"]["logistic"], CFG, events, 3.0, 5)
    assert t1 == t2                                                                                      # deterministic, independent of other participants
