"""Runtime warning manager (SYNTHETIC score sequences)."""
import numpy as np
import pytest

from parkistep.forecast.alerts import raise_alerts
from parkistep.twin.alerts import WarningManager, WarningPolicy
from parkistep.twin.model import ModelHandle, ThresholdInfo
from parkistep.twin.state import PredictionRecord

STRIDE = 0.2


def handle(thr=0.5, source="model_manifest"):
    return ModelHandle(status="available", model_id="m", horizon_s=3.0, threshold=ThresholdInfo(thr, source))


def pred(i, t, p, session="s"):
    return PredictionRecord(f"p{i}", "tw", "1", session, i, t, t - 2, t, p, 3.0, "m", "x", False, False, 0.5, "model_manifest", p >= 0.5,
                            "ok", {}, (), 0.0, "", i)


def mgr(k=1, merge=1.0, cd=0.0, cb=1, repeat=False, expire=None, max_active=None, thr=0.5, hist=None):
    pol = WarningPolicy(k, merge, cd, cb, repeat, expire, max_active, 1.5 * STRIDE, "config")
    return WarningManager("tw", "1", pol, handle(thr), hist)


def run(m, scores, t0=0.0, step=STRIDE, start=0, suppress=None):
    ev = []
    for j, p in enumerate(scores):
        ev += m.on_prediction(pred(start + j, round(t0 + j * step, 9), p), suppress)
    return ev


def kinds(ev):
    return [e.kind for e in ev]


def test_a_single_high_score_does_not_warn_when_persistence_requires_more():
    m = mgr(k=3)
    ev = run(m, [0.1, 0.9, 0.1, 0.9, 0.9, 0.1])
    assert "ACTIVATED" not in kinds(ev) and m.state.n_warnings == 0 and m.records == []
    assert kinds(ev).count("PENDING_CLEARED") >= 2


def test_pending_then_active_then_resolved_lifecycle():
    m = mgr(k=3, merge=0.5)
    run(m, [0.9]); assert m.state.state == "pending" and m.state.pending_count == 1
    run(m, [0.9, 0.9], t0=0.2, start=1)
    assert m.state.state == "active" and m.active.warning_id == "tw-w0001" and m.active.activated_at == pytest.approx(0.4)
    assert m.active.activation_prediction_id == "p2" and m.active.n_qualifying_forecasts == 3
    ev = run(m, [0.1] * 6, t0=0.6, start=3)
    assert "RESOLVED" in kinds(ev) and m.state.state == "none" and m.active is None
    w = m.records[0]
    assert w.status == "RESOLVED" and w.resolved_at is not None and [x["to"] for x in w.lifecycle] == ["ACTIVE", "RESOLVED"]


def test_persistence_one_activates_immediately_and_extends_while_high():
    m = mgr(k=1)
    ev = run(m, [0.9] * 5)
    assert kinds(ev).count("ACTIVATED") == 1 and m.records[0].n_qualifying_forecasts == 5 and m.records[0].max_score == 0.9


def test_cooldown_suppresses_close_repeats_and_is_recorded():
    m = mgr(k=1, merge=0.0, cd=3.0)
    ev = run(m, [0.9, 0.1, 0.9, 0.1, 0.9, 0.1, 0.1] + [0.1] * 20 + [0.9])
    assert kinds(ev).count("ACTIVATED") == 2 and kinds(ev).count("SUPPRESSED_COOLDOWN") >= 1 and m.state.n_suppressed_cooldown >= 1


def test_merge_gap_joins_nearby_runs_into_one_warning_distant_runs_are_separate():
    m = mgr(k=1, merge=1.0)
    run(m, [0.9, 0.1, 0.1, 0.9])                       # second run starts 0.6 s after the first ended: merged
    assert m.state.n_warnings == 1
    m2 = mgr(k=1, merge=0.2)
    ev = run(m2, [0.9, 0.1, 0.1, 0.1, 0.9])
    assert m2.state.n_warnings == 2 and "RESOLVED" in kinds(ev)


def test_allow_repeat_while_active_supersedes_but_default_extends():
    scores = [0.9, 0.1, 0.1, 0.1, 0.9]
    m = mgr(k=1, merge=0.5, cb=10)                     # recovery needs 10 below-threshold forecasts: warning stays active
    run(m, scores)
    assert m.state.n_warnings == 1 and m.active.n_qualifying_forecasts == 2
    m2 = mgr(k=1, merge=0.5, cb=10, repeat=True)
    ev = run(m2, scores)
    assert m2.state.n_warnings == 2 and "SUPERSEDED" in kinds(ev) and m2.records[0].resolution_reason.startswith("superseded")


def test_invalid_data_never_creates_a_warning_and_clears_pending():
    m = mgr(k=3)
    run(m, [0.9, 0.9])
    assert m.state.state == "pending"
    ev = m.on_invalid(0.4, "insufficient_sensor_quality")
    assert m.state.state == "none" and "PENDING_CLEARED" in kinds(ev)
    ev = run(m, [0.9, 0.9], t0=0.6, start=5)                     # needs three consecutive valid highs again
    assert "ACTIVATED" not in kinds(ev)


def test_active_warning_expires_without_valid_forecasts_and_by_max_age_history_kept():
    m = mgr(k=1, expire=1.0)
    run(m, [0.9])
    assert m.active is not None
    assert m.on_invalid(0.5, "x") == [] and m.active is not None
    ev = m.on_invalid(1.6, "x")
    assert kinds(ev) == ["EXPIRED"] and m.active is None and m.records[0].status == "EXPIRED"
    m2 = mgr(k=1, max_active=2.0, merge=100)
    run(m2, [0.9] * 15)
    assert m2.records[0].status == "EXPIRED" and "maximum active time" in m2.records[0].resolution_reason


def test_status_suppression_prevents_warning_creation():
    m = mgr(k=1)
    ev = run(m, [0.9, 0.9, 0.9], suppress="DEGRADED_DATA_QUALITY")
    assert "ACTIVATED" not in kinds(ev) and "SUPPRESSED_STATUS" in kinds(ev) and m.records == [] and m.state.n_suppressed_status == 1


def test_discontinuous_forecasts_do_not_count_as_consecutive():
    m = mgr(k=2)
    m.on_prediction(pred(0, 0.0, 0.9)); m.on_prediction(pred(1, 5.0, 0.9))       # 5 s apart
    assert m.state.n_warnings == 0
    m.on_prediction(pred(2, 5.2, 0.9))
    assert m.state.n_warnings == 1


def test_session_boundary_expires_warning_and_resets_policy_memory():
    m = mgr(k=1, cd=100.0)
    run(m, [0.9]); assert m.active is not None
    ev = m.on_session_boundary(1.0, "session ended (session_change)")
    assert kinds(ev) == ["EXPIRED"] and m.active is None
    run(m, [0.9], t0=1.2, start=9)
    assert m.state.n_warnings == 2                                  # cooldown does not carry into the next recording


def test_acknowledgement_is_recorded_without_changing_policy_state():
    m = mgr(k=1)
    run(m, [0.9])
    ev = m.acknowledge("tw-w0001", 0.1, "clinician-a")
    assert ev.kind == "ACKNOWLEDGED" and m.active is not None and m.records[0].acknowledged_by == "clinician-a"
    assert m.records[0].lifecycle[-1]["reason"] == "acknowledged"
    with pytest.raises(KeyError):
        m.acknowledge("nope", 0.2)


def test_warning_history_is_retained_and_trimmed_only_by_configuration():
    m = mgr(k=1, merge=0.0)
    run(m, [0.9, 0.1, 0.1] * 30)
    assert len(m.records) == 30
    m2 = mgr(k=1, merge=0.0, hist=5)
    run(m2, [0.9, 0.1, 0.1] * 30)
    assert len(m2.records) == 5 and m2.state.n_warnings == 30


def test_warning_records_carry_threshold_model_horizon_and_reason():
    m = mgr(k=2, thr=0.55)
    run(m, [0.9, 0.9])
    w = m.records[0]
    assert (w.threshold, w.threshold_source, w.model_id, w.horizon_s) == (0.55, "model_manifest", "m", 3.0)
    assert "2 consecutive" in w.reason and w.policy["persistence_anchors"] == 2 and "does not establish" in w.note


def test_manager_never_replaces_the_model_threshold():
    m = WarningManager("tw", "1", WarningPolicy(1, 1.0, 0.0, 1, False, None, None, 0.3, "config"), handle(0.8))
    ev = run(m, [0.7, 0.75, 0.79])
    assert m.state.n_warnings == 0 and m.threshold.value == 0.8
    run(m, [0.81], t0=1.0, start=10)
    assert m.state.n_warnings == 1


def test_without_a_threshold_nothing_alerts():
    m = WarningManager("tw", "1", WarningPolicy(1, 1.0, 0.0, 1, False, None, None, 0.3, "config"), handle(None, "unavailable"))
    ev = run(m, [0.99] * 5)
    assert kinds(ev) == ["THRESHOLD_UNAVAILABLE"] and m.records == []


def test_policy_from_manifest_vs_config():
    wcfg = {"policy_source": "model_manifest", "persistence_anchors": 9, "merge_gap_s": 9.0, "cooldown_s": 9.0, "clear": {"consecutive_below": 2},
            "allow_repeat_while_active": False, "expire_after_invalid_s": None, "max_active_s": None}
    h = ModelHandle(status="available", alert_policy={"persistence_anchors": 2, "merge_gap_s": 1.5, "cooldown_s": 4.0})
    p = WarningPolicy.from_settings(wcfg, h, 0.2)
    assert (p.persistence_anchors, p.merge_gap_s, p.cooldown_s, p.consecutive_below, p.source) == (2, 1.5, 4.0, 2, "model_manifest")
    p = WarningPolicy.from_settings({**wcfg, "policy_source": "config"}, h, 0.2)
    assert (p.persistence_anchors, p.merge_gap_s, p.cooldown_s) == (9, 9.0, 9.0) and p.continuity_s == pytest.approx(0.3)


@pytest.mark.parametrize("seed", range(6))
def test_runtime_policy_reproduces_the_phase2_retrospective_alerts(seed):
    """The online manager must give exactly the alert times of Phase 2's raise_alerts on the same score sequence
    (persistence, merging, cooldown, interrupted series), so runtime and retrospective evaluation cannot drift apart."""
    rng = np.random.default_rng(seed)
    bad = 0
    for _ in range(250):
        k = int(rng.integers(1, 4)); merge = float(rng.choice([0.0, 0.4, 1.0, 2.0])); cd = float(rng.choice([0.0, 0.0, 1.5, 5.0]))
        n = int(rng.integers(20, 160))
        t = np.round(np.arange(n) * STRIDE, 10)
        if rng.random() < 0.3:
            t = t + np.cumsum(rng.random(n) < 0.05) * 1.0                      # interruptions in the forecast series
        p = np.where(rng.random(n) < rng.choice([0.1, 0.3, 0.5]), 0.9, 0.1)
        ref = raise_alerts(t, p, 0.5, STRIDE, {"persistence_anchors": k, "merge_gap_s": merge, "cooldown_s": cd})
        m = mgr(k=k, merge=merge, cd=cd)
        got = [e.ts for i, (ti, pi) in enumerate(zip(t, p)) for e in m.on_prediction(pred(i, float(ti), float(pi))) if e.kind == "ACTIVATED"]
        if len(got) != len(ref) or not np.allclose(sorted(got), sorted(ref)):
            bad += 1
    assert bad == 0
