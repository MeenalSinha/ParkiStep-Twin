"""Rolling buffer, ingestion contract and ordering policies (SYNTHETIC data, scripted predictor test double)."""
import math

import numpy as np
import pytest

from p3helpers import DT, N_WIN, STRIDE_S, channels, make_engine, rec, settings, stream
from parkistep.twin.buffer import ObservationMapper, ReorderStage, RollingBuffer, Observation
from parkistep.twin.errors import ModelUnavailableError


def feed(eng, s, n, pid=1, **kw):
    return [eng.ingest(pid, r) for r in stream(s, n, patient=pid, **kw)]


def test_incomplete_window_never_reaches_the_model(tmp_path):
    eng, stub, s = make_engine(tmp_path)
    eng.create_twin(1)
    feed(eng, s, N_WIN - 1)
    assert stub.calls == [] and eng.get_twin(1).status == "WARMING_UP" and eng.latest_prediction(1) is None
    feed_more = eng.ingest(1, rec(s, N_WIN - 1))
    assert len(stub.calls) == 1 and feed_more.prediction is not None and eng.get_twin(1).status == "READY"


def test_window_passed_to_model_is_exactly_the_trailing_window(tmp_path):
    eng, stub, s = make_engine(tmp_path)
    eng.create_twin(1)
    feed(eng, s, 60)
    for c in stub.calls:
        w = c["window"]
        assert len(w) == N_WIN and list(w.columns) == ["timestamp_s"] + channels(s)
        assert np.all(np.diff(w.timestamp_s) > 0)
    last = stub.calls[-1]["window"]
    assert last.timestamp_s.iloc[-1] <= 59 * DT + 1e-9


def test_cadence_matches_stride_and_no_window_is_scored_twice(tmp_path):
    eng, stub, s = make_engine(tmp_path)
    eng.create_twin(1)
    n = 80
    feed(eng, s, n)
    steps = int(round(STRIDE_S / DT))
    assert len(stub.calls) == (n - N_WIN) // steps + 1
    ends = [round(float(c["window"].timestamp_s.iloc[-1]), 6) for c in stub.calls]
    assert len(set(ends)) == len(ends) and np.allclose(np.diff(ends), STRIDE_S)


def test_cadence_is_configurable(tmp_path):
    eng, stub, s = make_engine(tmp_path, "cadence.every_s=0.5")
    eng.create_twin(1)
    feed(eng, s, 60)
    ends = [float(c["window"].timestamp_s.iloc[-1]) for c in stub.calls]
    assert np.allclose(np.diff(ends), 0.5)


def test_buffer_memory_is_bounded(tmp_path):
    eng, stub, s = make_engine(tmp_path, "buffer.max_observations=30")
    eng.create_twin(1)
    feed(eng, s, 400)
    assert eng.get_twin(1).stream.n_buffered <= 30
    assert len(eng._rt(1).buffer) <= 30
    assert len(eng.recent_observations(1, 1000)) <= 30


def test_buffer_smaller_than_model_window_is_rejected_at_configuration(tmp_path):
    with pytest.raises(ModelUnavailableError, match="max_observations"):
        make_engine(tmp_path, "buffer.max_observations=5")
    with pytest.raises(ModelUnavailableError, match="max_duration_s"):
        make_engine(tmp_path, "buffer.max_duration_s=0.5")


def test_warmup_extra_delays_first_forecast(tmp_path):
    eng, stub, s = make_engine(tmp_path, "buffer.warmup_extra_s=1.0")
    eng.create_twin(1)
    feed(eng, s, 30)
    first = float(stub.calls[0]["window"].timestamp_s.iloc[-1])
    assert first >= WINDOW_PLUS(1.0) - 1e-9


def WINDOW_PLUS(extra):
    return 1.0 + extra


def test_session_change_resets_buffer_even_when_timestamps_are_continuous(tmp_path):
    eng, stub, s = make_engine(tmp_path)
    eng.create_twin(1)
    for i in range(40):
        eng.ingest(1, rec(s, i, session="r1"))
    for i in range(40, 80):                                    # timestamps continue seamlessly, only the session id changes
        eng.ingest(1, rec(s, i, session="r2"))
    ev = [e.kind for e in eng.event_history(1)]
    assert "SESSION_END" in ev and ev.count("SESSION_START") == 2
    boundary_t = 40 * DT
    for c in stub.calls:
        ts = c["window"].timestamp_s.to_numpy()
        assert ts.max() < boundary_t - 1e-9 or ts.min() >= boundary_t - 1e-9, "a window spans two recordings"
    first_r2 = min(float(c["window"].timestamp_s.iloc[-1]) for c in stub.calls if c["window"].timestamp_s.iloc[-1] >= boundary_t - 1e-9)
    assert first_r2 >= boundary_t + (N_WIN - 1) * DT - 1e-9
    assert eng.get_twin(1).stream.session_id == "r2"
    assert all(p.session_id in ("r1", "r2") for p in eng.recent_predictions(1, 100))


def test_require_explicit_end_policy(tmp_path):
    eng, stub, s = make_engine(tmp_path, "ingestion.session_change_policy=require_explicit_end")
    eng.create_twin(1)
    feed(eng, s, 5, session="r1")
    r = eng.ingest(1, rec(s, 5, session="r2"))
    assert r.status == "rejected" and r.reason == "SESSION_CHANGE_REQUIRES_END"
    eng.end_session(1)
    assert eng.ingest(1, rec(s, 5, session="r2")).accepted


def test_other_patients_records_never_enter_a_twin(tmp_path):
    eng, stub, s = make_engine(tmp_path)
    eng.create_twin(1); eng.create_twin(2)
    for i in range(40):
        r = rec(s, i, patient=1); r["ankleL_acc_x"] = 1000.0 + i          # patient-1 marker
        eng.ingest(1, r)
        r2 = rec(s, i, patient=2); r2["ankleL_acc_x"] = 2000.0 + i        # patient-2 marker
        eng.ingest(2, r2)
    for c in stub.calls:
        pid = c["metadata"]["twin_id"]
        base = 1000.0 if pid == "twin-1" else 2000.0
        assert c["window"].ankleL_acc_x.between(base, base + 100).all()
    bad = eng.ingest(1, rec(s, 99, patient=2))
    assert bad.status == "rejected" and bad.reason == "PATIENT_MISMATCH"
    assert eng.get_twin(1).stream.n_accepted_total == 40 and eng.get_twin(2).stream.n_accepted_total == 40


def test_large_gap_resets_window_and_requires_new_warmup(tmp_path):
    eng, stub, s = make_engine(tmp_path)
    eng.create_twin(1)
    feed(eng, s, 30)
    n_before = len(stub.calls)
    t_gap = 30 * DT + 0.5
    ok = []
    for i in range(30):
        ok.append(eng.ingest(1, rec(s, 0, ts=t_gap + i * DT)))
    kinds = [e.kind for e in eng.event_history(1)]
    assert "GAP_DETECTED" in kinds and "GAP_RESET" in kinds
    for c in stub.calls[n_before:]:
        ts = c["window"].timestamp_s.to_numpy()
        assert ts.min() >= t_gap - 1e-9, "post-gap window contains pre-gap samples"
    first_after = min(float(c["window"].timestamp_s.iloc[-1]) for c in stub.calls[n_before:])
    assert first_after >= t_gap + (N_WIN - 1) * DT - 1e-9


def test_gap_flag_only_keeps_samples_but_window_is_ineligible_until_gap_leaves(tmp_path):
    eng, stub, s = make_engine(tmp_path, "ingestion.gap_policy=flag_only")
    eng.create_twin(1)
    feed(eng, s, 30)
    n_before = len(stub.calls)
    t_gap = 30 * DT + 0.5
    reasons = []
    for i in range(30):
        r = eng.ingest(1, rec(s, 0, ts=t_gap + i * DT))
        reasons += [u.forecast_unavailable_reason for u in r.updates if u.forecast_unavailable_reason]
    assert "gap_in_window" in reasons
    for c in stub.calls[n_before:]:
        ts = c["window"].timestamp_s.to_numpy()
        assert np.max(np.diff(ts)) < 0.2, "model was called on a window containing the gap"


def test_duplicate_policies(tmp_path):
    eng, stub, s = make_engine(tmp_path)
    eng.create_twin(1)
    eng.ingest(1, rec(s, 0)); eng.ingest(1, rec(s, 1))
    r = eng.ingest(1, rec(s, 1))
    assert r.status == "rejected" and r.reason == "DUPLICATE_TIMESTAMP" and eng.get_twin(1).stream.n_accepted_total == 2
    eng2, _, s2 = make_engine(tmp_path / "b", "ingestion.duplicate_policy=ignore_identical")
    eng2.create_twin(1)
    eng2.ingest(1, rec(s2, 0)); eng2.ingest(1, rec(s2, 1))
    same = eng2.ingest(1, rec(s2, 1))
    assert same.status == "ignored" and eng2.get_twin(1).stream.n_accepted_total == 2
    other = rec(s2, 1, seed=5)
    r3 = eng2.ingest(1, other)
    assert r3.status == "rejected" and r3.reason == "CONFLICTING_DUPLICATE"


def test_out_of_order_rejected_by_default_and_arrival_order_not_hidden(tmp_path):
    eng, stub, s = make_engine(tmp_path)
    eng.create_twin(1)
    for i in (0, 1, 2, 4):
        eng.ingest(1, rec(s, i))
    late = eng.ingest(1, rec(s, 3))
    assert late.status == "rejected" and late.reason == "OUT_OF_ORDER"
    assert eng.get_twin(1).stream.rejected_counts == {"OUT_OF_ORDER": 1}
    assert [o["timestamp"] for o in eng.recent_observations(1, 10)] == pytest.approx([0, 0.1, 0.2, 0.4])
    assert any(e.kind == "OBSERVATION_REJECTED" and e.detail["reason"] == "OUT_OF_ORDER" for e in eng.event_history(1))


def test_reorder_policy_releases_in_order_within_tolerance_and_records_it(tmp_path):
    eng, stub, s = make_engine(tmp_path, "ingestion.out_of_order.policy=reorder_within_tolerance",
                               "ingestion.out_of_order.reorder_tolerance_s=0.25")
    eng.create_twin(1)
    order = [0, 1, 2, 4, 3, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20]
    for i in order:
        eng.ingest(1, rec(s, i))
    late = eng.ingest(1, rec(s, 0, ts=0.05))                     # older than anything already released to the buffer
    assert late.status == "rejected" and late.reason == "TOO_LATE_TO_REORDER"
    eng.end_session(1)                                           # flushes the hold-back
    st = eng.get_twin(1)
    assert st.stream.n_accepted_total == len(order)
    assert any(e.kind == "OBSERVATION_REORDERED" for e in eng.event_history(1))
    assert len(stub.calls) > 0
    for c in stub.calls:
        assert np.all(np.diff(c["window"].timestamp_s) > 0), "released samples must be in timestamp order"


def test_reorder_stage_unit_bounds_and_too_late():
    st = ReorderStage(0.25, max_held=3)
    mk = lambda t, k: Observation("1", "r", t, {}, arrival_seq=k)
    out = []
    for k, t in enumerate([0.0, 0.1, 0.3, 0.2, 0.9]):
        reason, rel = st.offer(mk(t, k), out[-1].timestamp if out else None)
        assert reason is None
        out += rel
    assert [o.timestamp for o in out] == sorted(o.timestamp for o in out)
    assert any(o.reordered for o in out + st.flush())
    reason, _ = ReorderStage(0.25, 3).offer(mk(0.1, 9), released_floor=0.5)
    assert reason == "TOO_LATE_TO_REORDER"
    st2 = ReorderStage(100.0, max_held=2)                         # held count is capped even if nothing is "old enough"
    got = []
    for k in range(10):
        got += st2.offer(mk(float(k), k), None)[1]
    assert len(st2) <= 2 and len(got) == 8


def test_batch_ingestion_equals_sequential(tmp_path):
    a, sa, s = make_engine(tmp_path / "a", score=lambda t: 0.9 if int(t * 10) % 7 < 3 else 0.1)
    b, sb, _ = make_engine(tmp_path / "b", score=lambda t: 0.9 if int(t * 10) % 7 < 3 else 0.1)
    recs = stream(s, 70)
    recs.insert(30, dict(recs[29]))                              # a duplicate in the middle
    recs.insert(50, {"participant_id": 1})                       # an unusable record
    a.create_twin(1); b.create_twin(1)
    ra = a.ingest_many(1, recs)
    rb = [b.ingest(1, r) for r in recs]
    assert [(x.status, x.reason) for x in ra] == [(x.status, x.reason) for x in rb]
    da, db = a.get_twin(1).to_dict(), b.get_twin(1).to_dict()
    for d in (da, db):
        d["config"].pop("config_hash")                           # differs only because the two tests use different temp paths
    assert da == db and da["config"]["compat_hash"]
    strip = lambda ps: [{k: v for k, v in p.to_dict().items() if k != "inference_ms"} for p in ps]      # inference_ms is measured wall time
    assert strip(a.recent_predictions(1, 999)) == strip(b.recent_predictions(1, 999)) and len(a.recent_predictions(1, 999)) > 5
    assert [w.to_dict() for w in a.warning_history(1)] == [w.to_dict() for w in b.warning_history(1)]


def test_missing_and_nonfinite_values_are_never_fabricated(tmp_path):
    eng, stub, s = make_engine(tmp_path)
    eng.create_twin(1)
    r = rec(s, 0); del r["wrist_acc_x"]; r["back_acc_y"] = float("inf"); r["back_acc_z"] = "garbage"
    out = eng.ingest(1, r)
    assert out.accepted
    last = eng.get_twin(1).stream.latest_values
    assert last["wrist_acc_x"] is None and last["back_acc_y"] is None and last["back_acc_z"] is None
    assert any(e.kind == "DATA_QUALITY" for e in eng.event_history(1))
    eng2, _, s2 = make_engine(tmp_path / "r", "ingestion.missing_channel_policy=reject", "ingestion.nonfinite_policy=reject")
    eng2.create_twin(1)
    r2 = rec(s2, 0); del r2["wrist_acc_x"]
    assert eng2.ingest(1, r2).reason == "MISSING_CHANNEL"
    r3 = rec(s2, 0); r3["back_acc_y"] = "garbage"
    assert eng2.ingest(1, r3).reason == "INVALID_VALUE"


def test_dead_sensor_position_blocks_inference_and_marks_degraded(tmp_path):
    eng, stub, s = make_engine(tmp_path)
    eng.create_twin(1)
    for i in range(40):
        eng.ingest(1, rec(s, i, nan_channels=("wrist",)))
    assert stub.calls == []
    st = eng.get_twin(1)
    assert st.status == "DEGRADED_DATA_QUALITY" and st.forecast.status == "unavailable"
    assert "wrist" in st.stream.quality_detail["failing_positions"]
    assert eng.latest_prediction(1) is None and st.warning.n_warnings == 0


def test_isolated_missing_sample_follows_the_phase2_valid_fraction_rule(tmp_path):
    """Phase 2 requires >= 95% valid raw samples per window. With an 11-sample window one missing sample (91%) fails the
    rule -> no forecast while it is in the window, recovery afterwards; with a 41-sample window (97.6%) it passes."""
    eng, stub, s = make_engine(tmp_path)
    eng.create_twin(1)
    statuses = []
    for i in range(40):
        r = rec(s, i)
        if i == 20:
            r["ankleL_acc_x"] = float("nan")
        eng.ingest(1, r)
        statuses.append(eng.get_twin(1).status)
    assert "DEGRADED_DATA_QUALITY" in statuses and statuses[-1] == "READY"
    bad_ends = [float(c["window"].timestamp_s.iloc[-1]) for c in stub.calls]
    assert not any(20 * DT - 1e-9 <= e < 20 * DT + (N_WIN - 1) * DT - 1e-9 for e in bad_ends), "model called on a window holding the gap"
    eng2, stub2, s2 = make_engine(tmp_path / "wide", manifest_kw={"window_s": 4.0})
    eng2.create_twin(1)
    for i in range(70):
        r = rec(s2, i)
        if i == 50:
            r["ankleL_acc_x"] = float("nan")
        eng2.ingest(1, r)
    assert len(stub2.calls) > 0 and eng2.get_twin(1).status == "READY"
    assert any(c["window"].ankleL_acc_x.isna().any() for c in stub2.calls), "the engine must pass the missing sample through untouched (filling is the predictor's job)"


@pytest.mark.parametrize("bad", ["not-a-number", float("nan"), float("inf"), None, True])
def test_invalid_timestamps_are_rejected_not_guessed(tmp_path, bad):
    eng, stub, s = make_engine(tmp_path)
    eng.create_twin(1)
    r = rec(s, 0); r["timestamp_s"] = bad
    out = eng.ingest(1, r)
    assert out.status == "rejected" and out.reason in ("INVALID_TIMESTAMP", "MISSING_FIELD")
    assert eng.get_twin(1).stream.n_accepted_total == 0


def test_timestamp_units_and_datetime_interpretation(tmp_path):
    s_ms = settings(tmp_path, "observation.timestamp.unit=milliseconds")
    m = ObservationMapper(s_ms.cfg["observation"], s_ms.cfg["ingestion"], tuple(channels(s_ms)))
    assert m.parse_timestamp(1500) == pytest.approx(1.5)
    s_dt = settings(tmp_path, "observation.timestamp.kind=datetime", "observation.timestamp.timezone=Asia/Kolkata")
    m = ObservationMapper(s_dt.cfg["observation"], s_dt.cfg["ingestion"], tuple(channels(s_dt)))
    naive = m.parse_timestamp("2026-01-01T05:30:00")                  # interpreted in the configured timezone
    utc = m.parse_timestamp("2026-01-01T00:00:00Z")
    assert naive == pytest.approx(utc)
    assert m.parse_timestamp("garbage") is None


def test_sensor_channel_mapping_is_configurable(tmp_path):
    def mutate(cfg):
        cfg["observation"]["channels"] = {"mode": "mapping", "mapping": {f"dev_{c}": c for c in channels_of(cfg)}}
        cfg["observation"]["patient_field"] = "pid"
        cfg["observation"]["timestamp"]["field"] = "t"
        cfg["observation"]["session_fields"] = ["run"]

    def channels_of(_cfg):
        from p3helpers import settings as _s
        return channels(_s(tmp_path))
    eng, stub, s = make_engine(tmp_path, mutate=mutate)
    eng.create_twin("7")
    for i in range(30):
        base = rec(s, i)
        row = {"pid": 7, "t": base["timestamp_s"], "run": "x"} | {f"dev_{c}": base[c] for c in channels(s)}
        assert eng.ingest("7", row).accepted
    assert len(stub.calls) > 0 and set(channels(s)) <= set(stub.calls[0]["window"].columns)
