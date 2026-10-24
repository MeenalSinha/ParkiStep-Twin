import numpy as np
import pandas as pd
import pytest

from parkistep.forecast.alerts import (aggregate_event_metrics, match_alerts, raise_alerts, score_predictions, select_threshold)

CFG = {"persistence_anchors": 1, "merge_gap_s": 0.0, "cooldown_s": 0.0, "min_lead_s": 0.0, "extra_alerts_for_matched_event": "ignore"}


def test_alert_merging_persistence_cooldown():
    t = np.arange(0, 3.0, 0.2); p = np.zeros(len(t)); p[[2, 3, 4, 8, 9]] = 1.0
    assert raise_alerts(t, p, 0.5, 0.2, CFG) == [pytest.approx(0.4), pytest.approx(1.6)]            # runs of consecutive anchors = one alert
    assert len(raise_alerts(t, p, 0.5, 0.2, {**CFG, "merge_gap_s": 1.0})) == 1                      # close runs merged
    assert raise_alerts(t, p, 0.5, 0.2, {**CFG, "persistence_anchors": 3}) == [pytest.approx(0.8)]  # only the 3-run qualifies
    assert len(raise_alerts(t, p, 0.5, 0.2, {**CFG, "cooldown_s": 2.0})) == 1


def test_alert_runs_break_at_missing_anchors():
    t = np.array([0.0, 0.2, 0.4, 1.4, 1.6]); p = np.ones(5)                    # gap between 0.4 and 1.4 (ineligible anchors)
    assert len(raise_alerts(t, p, 0.5, 0.2, CFG)) == 2


def test_valid_warning_precedes_onset_and_lead_is_positive():
    onsets = np.array([10.0]); ids = np.array(["e0"])
    m = match_alerts([7.5], onsets, ids, {"e0"}, 3.0, 0.0, "ignore")
    assert m["detected"] == {"e0": pytest.approx(2.5)} and m["n_false"] == 0
    assert match_alerts([10.2], onsets, ids, {"e0"}, 3.0, 0.0, "ignore")["detected"] == {}           # after onset: not a warning
    assert match_alerts([10.2], onsets, ids, {"e0"}, 3.0, 0.0, "ignore")["n_false"] == 1
    assert match_alerts([5.0], onsets, ids, {"e0"}, 3.0, 0.0, "ignore")["n_false"] == 1              # too early for the horizon
    assert match_alerts([9.8], onsets, ids, {"e0"}, 3.0, 0.5, "ignore")["n_false"] == 1              # inside min_lead


def test_extra_alerts_for_one_event_follow_policy():
    onsets = np.array([10.0]); ids = np.array(["e0"])
    a = [8.0, 9.0]
    assert match_alerts(a, onsets, ids, {"e0"}, 3.0, 0, "ignore")["n_false"] == 0
    assert match_alerts(a, onsets, ids, {"e0"}, 3.0, 0, "false")["n_false"] == 1
    assert match_alerts(a, onsets, ids, {"e0"}, 3.0, 0, "ignore")["n_redundant"] == 1


def _pred(rid="r", pid=1, probs=None, ys=None, ev=None, thr=0.5, t0=0.0, stride=0.2):
    n = len(probs)
    return pd.DataFrame({"recording_id": rid, "participant_id": pid, "anchor_t": t0 + stride * np.arange(n), "prob": probs,
                         "y": ys if ys is not None else 0, "event_id": ev if ev is not None else "", "threshold": thr})


def test_false_alerts_per_hour_uses_documented_denominator():
    # 100 anchors x 0.2 s = 20 s monitored; two alerts far from any onset
    p = np.zeros(100); p[[10, 60]] = 1
    pred = _pred(probs=p)
    events = pd.DataFrame({"event_id": [], "recording_id": [], "onset_t": [], "participant_id": []})
    m = aggregate_event_metrics(score_predictions(pred, events, 3.0, 0.2, CFG))
    assert m["n_false_alerts"] == 2 and m["monitored_hours"] == pytest.approx(20 / 3600)
    assert m["false_alerts_per_hour"] == pytest.approx(2 / (20 / 3600))
    assert m["event_sensitivity"] is None                                    # no evaluable events -> not invented


def test_event_sensitivity_and_lead_distribution():
    p = np.zeros(100); p[40] = 1                                             # alert at t=8.0
    ys = np.zeros(100, int); ys[40:55] = 1
    ev = np.array([""] * 100, dtype=object); ev[40:55] = "r-e0"
    pred = _pred(probs=p, ys=ys, ev=ev)
    events = pd.DataFrame({"event_id": ["r-e0"], "recording_id": ["r"], "onset_t": [10.0], "participant_id": [1]})
    m = aggregate_event_metrics(score_predictions(pred, events, 3.0, 0.2, CFG))
    assert m["event_sensitivity"] == 1.0 and m["lead_time_s"]["median"] == pytest.approx(2.0) and m["n_false_alerts"] == 0


def test_alert_for_unevaluable_event_is_not_a_false_alert():
    p = np.zeros(100); p[40] = 1
    pred = _pred(probs=p)                                                    # no positive anchors -> event not evaluable
    events = pd.DataFrame({"event_id": ["r-e0"], "recording_id": ["r"], "onset_t": [10.0], "participant_id": [1]})
    m = aggregate_event_metrics(score_predictions(pred, events, 3.0, 0.2, CFG))
    assert m["n_false_alerts"] == 0 and m["n_events_evaluable"] == 0


def test_threshold_selection_only_uses_given_rows_and_respects_budget():
    rng = np.random.RandomState(0)
    n = 400; ys = np.zeros(n, int); ev = np.array([""] * n, dtype=object); ys[100:110] = 1; ev[100:110] = "r-e0"
    prob = rng.uniform(size=n) * 0.4; prob[100:110] += 0.5
    pred = _pred(probs=prob, ys=ys, ev=ev)
    events = pd.DataFrame({"event_id": ["r-e0"], "recording_id": ["r"], "onset_t": [pred.anchor_t[110]], "participant_id": [1]})
    tcfg = {"policy": "fa_budget", "target_false_alerts_per_hour": 30.0, "grid_size": 20}
    sel = select_threshold(pred, events, 3.0, 0.2, CFG, tcfg)
    assert sel["inner_false_alerts_per_hour"] <= 30.0 and 0.0 < sel["threshold"] < 1.5
    assert select_threshold(pred, events, 3.0, 0.2, CFG, {"policy": "fixed", "fixed_value": 0.7})["threshold"] == 0.7
    const = select_threshold(_pred(probs=np.full(50, 0.3)), events, 3.0, 0.2, CFG, tcfg)
    assert const["threshold"] > 0.3                                          # constant scores: alerts disabled, not invented
