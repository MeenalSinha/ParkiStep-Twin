"""Phase 4 backend tests. SYNTHETIC data + scripted predictor test double (from p3helpers) unless a test says otherwise."""
import json

import numpy as np
import pandas as pd
import pytest

from p3helpers import DT, make_engine, stream
from parkistep.config import ConfigError
from parkistep.dashboard import analysis
from parkistep.dashboard.config import load_phase4
from parkistep.dashboard.performance import PerformanceReports
from parkistep.dashboard.plotdata import coverage_summary, decimate, insert_gaps, signal_traces
from parkistep.dashboard.replay_session import ReplaySession, SessionStatus
from parkistep.twin.replay import ReplayRunner
from parkistep.twin.streams import GroundTruth, SensorStream
from pathlib import Path

CFG = Path(__file__).resolve().parents[1] / "configs" / "phase4.yaml"


class ListStream(SensorStream):
    def __init__(self, records):
        self.r = records

    def records(self):
        return iter(self.r)

    def describe(self):
        return {"kind": "test", "fields": sorted(self.r[0]), "n_records": len(self.r)}


def session(tmp_path, n=300, score=0.1, **kw):
    eng, stub, s = make_engine(tmp_path, score=score, **kw)
    eng.create_twin(1)
    recs = stream(s, n)
    truth = GroundTruth(pd.DataFrame(columns=["event_id", "onset_t", "end_t", "recording_id"]), ["r1"], {"r1": (0.0, n * DT)})
    return eng, s, recs, ReplaySession(session_id="t", engine=eng, patient_id="1", recording_id="r1", stream=ListStream(recs), truth=truth,
                                       max_stored_observations=10_000)


# ---------------------------------------------------------------- configuration
def test_config_loads_and_status_styles_resolve():
    s = load_phase4(CFG)
    assert s.style("twin", "READY")["label"] and s.color("alert").startswith("#")
    assert s.style("twin", "NOT_A_STATUS")["role"] == "neutral"      # unknown status is explicit, not an exception


def test_invalid_config_gives_actionable_error():
    with pytest.raises(ConfigError, match="replay.speed_choices"):
        load_phase4(CFG, overrides=["replay.speed_choices=[]"])
    with pytest.raises(ConfigError, match="theme colour role"):
        load_phase4(CFG, overrides=["status_styles.twin.READY.role=nonexistent"])
    with pytest.raises(ConfigError):
        load_phase4(CFG.with_name("missing.yaml"))


def test_overrides_change_behaviour_without_code_edits():
    assert load_phase4(CFG, overrides=["plots.history_s=33"])["plots"]["history_s"] == 33


# ---------------------------------------------------------------- plot data
def test_decimate_keeps_ends_and_limit():
    df = pd.DataFrame({"t": np.arange(1000.0), "y": np.arange(1000.0)})
    d = decimate(df, 100)
    assert len(d) <= 101 and d["t"].iloc[0] == 0 and d["t"].iloc[-1] == 999
    assert decimate(df, 5000) is df


def test_gaps_become_explicit_breaks_and_missing_values_stay_nan():
    t = np.r_[np.arange(0, 1, 0.1), np.arange(3, 4, 0.1)]
    df = pd.DataFrame({"timestamp_s": t, "a": np.arange(len(t), dtype=float)})
    df.loc[2, "a"] = np.nan
    out = insert_gaps(df, "timestamp_s", 2.0)
    assert len(out) == len(df) + 1 and out["a"].isna().sum() == 2        # one inserted break + the original missing value
    cov = coverage_summary(df, ["a"])
    assert len(cov["gaps"]) == 1 and cov["gaps"][0]["duration_s"] == pytest.approx(2.1) and cov["missing_by_channel"] == {"a": 1}


def test_traces_come_from_supplied_observations_only():
    df = pd.DataFrame({"timestamp_s": np.arange(0, 5, 0.1), "x": np.arange(50.0)})
    tr = signal_traces(df, [{"canonical": "x", "position": "p", "signal": "acc", "axis": "x", "unit": "g"}], max_points=1000)
    assert len(tr) == 1 and np.array_equal(tr[0]["y"], df["x"].to_numpy()) and signal_traces(df.iloc[0:0], [], max_points=10) == []


# ---------------------------------------------------------------- retrospective timing
def test_post_onset_warning_is_not_an_early_warning():
    ev = [{"event_id": "e1", "onset_t": 10.0, "end_t": 12.0, "recording": "r1"}]
    w = [{"warning_id": "a", "activated_at": 8.0, "session_id": "r1"}, {"warning_id": "b", "activated_at": 11.0, "session_id": "r1"},
         {"warning_id": "c", "activated_at": 13.0, "session_id": "r1"}, {"warning_id": "d", "activated_at": 30.0, "session_id": "r1"}]
    out = {x["warning_id"]: x for x in analysis.classify_warnings(w, ev, horizon_s=3.0, input_window_s=2.0)}
    assert out["a"]["category"] == analysis.PRE_ONSET and out["a"]["lead_s"] == pytest.approx(2.0)
    assert out["b"]["category"] == analysis.DURING and out["b"]["lead_s"] is None
    assert out["c"]["category"] == analysis.OVERLAP and out["d"]["category"] == analysis.NONE_IN_HORIZON


# ---------------------------------------------------------------- performance reader
def test_performance_reads_saved_numbers_and_keeps_missing_missing(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"experiment_id": "x", "results": {"sensor_only|m|h3": {"experiment": "sensor_only", "model": "m", "horizon_s": 3.0,
                "window": {"n_windows": 10, "average_precision": 0.5}, "event": {"n_events_evaluable": 4, "n_events_detected": 1, "event_sensitivity": 0.25,
                "monitored_hours": 2.0, "n_false_alerts": 6, "false_alerts_per_hour": 3.0, "lead_time_s": None}, "n_participants": 3}}}))
    r = PerformanceReports(p).rows()[0]
    assert r["event_sensitivity"] == 0.25 and r["n_events_evaluable"] == 4 and r["roc_auc"] is None and r["median_lead_s"] is None
    assert PerformanceReports(p).calibration_status("sensor_only", "m", 3.0)["validated_calibrated"] is False


def test_performance_missing_or_corrupt_file_is_reported(tmp_path):
    assert "not found" in PerformanceReports(tmp_path / "nope.json").problem
    bad = tmp_path / "b.json"
    bad.write_text("{not json")
    assert not PerformanceReports(bad).available


# ---------------------------------------------------------------- replay session
def test_nothing_is_offered_before_start_and_start_is_idempotent(tmp_path):
    eng, s, recs, sess = session(tmp_path)
    assert sess.advance(sim_seconds=1).offered == 0 and sess.offered == 0
    sess.start(); sess.start()
    sess.advance(max_observations=10)
    assert sess.offered == 10 and eng.get_twin("1").stream.n_accepted_session == 10


def test_repeated_advance_never_reoffers_observations(tmp_path):
    eng, s, recs, sess = session(tmp_path, n=200)
    sess.start()
    total = 0
    while not sess.is_finished:
        total += sess.advance(sim_seconds=0.7, max_observations=37).offered
    assert total == 200 and sess.offered == 200 and sess.status == SessionStatus.COMPLETED
    assert sess.advance(max_observations=50).offered == 0                 # a rerun after completion offers nothing
    assert eng.get_twin("1").stream.n_accepted_total == 200
    assert sess.progress().observations_rejected == 0


def test_sliced_replay_equals_blocking_replay(tmp_path):
    scorer = lambda t: 0.9 if 3 <= t < 4 else 0.1
    eng, s, recs, sess = session(tmp_path / "a", n=300, score=scorer)
    sess.start()
    while not sess.is_finished:
        sess.advance(sim_seconds=1.3, max_observations=41)
    eng2, stub2, s2 = make_engine(tmp_path / "b", score=scorer)
    eng2.create_twin(1)
    blocking = ReplayRunner(s2, eng2).run("1", ListStream(stream(s2, 300)))
    assert sess.summary["determinism_digest"] == blocking["determinism_digest"]
    assert sess.summary["counts"]["predictions"] == blocking["counts"]["predictions"]


def test_pause_resume_stop(tmp_path):
    eng, s, recs, sess = session(tmp_path)
    sess.start(); sess.advance(max_observations=20)
    sess.pause()
    assert sess.status == SessionStatus.PAUSED and sess.advance(max_observations=20).offered == 0 and sess.offered == 20
    sess.resume(); sess.advance(max_observations=20)
    assert sess.offered == 40
    sess.stop()
    assert sess.status == SessionStatus.STOPPED and sess.summary["partial_replay"] is True and sess.stop() == SessionStatus.STOPPED
    assert eng.get_twin("1").stream.session_id is None                    # the engine ended its session


def test_ground_truth_is_withheld_until_the_session_ends(tmp_path):
    eng, s, recs, sess = session(tmp_path, n=100)
    sess.start(); sess.advance(max_observations=50)
    assert sess.retrospective() is None
    sess.advance()
    assert sess.status == SessionStatus.COMPLETED and sess.retrospective() is not None


def test_stream_records_carry_no_annotation_fields(tmp_path):
    eng, s, recs, sess = session(tmp_path)
    assert not ({"fog", "fog_severity", "activity", "task"} & set(recs[0]))


def test_failure_is_reported_not_hidden(tmp_path):
    eng, s, recs, sess = session(tmp_path)
    recs[5] = {**recs[5], "unexpected_field": 1}                        # a field outside the declared set
    sess = ReplaySession(session_id="t", engine=eng, patient_id="1", recording_id="r1",
                         stream=type("S", (ListStream,), {"describe": lambda self: {"fields": sorted(recs[0]), "n_records": len(recs)}})(recs),
                         truth=GroundTruth(pd.DataFrame(), ["r1"], {}), max_stored_observations=100)
    sess.start(); sess.advance()
    assert sess.status == SessionStatus.FAILED and "outside the declared set" in sess.error["message"] and sess.summary is None


def test_observation_store_is_bounded_and_flagged(tmp_path):
    eng, s, recs, sess = session(tmp_path, n=100)
    sess._max_stored = 30
    sess.start(); sess.advance()
    assert len(sess.observations_frame()) == 30 and sess.store_truncated
    assert len(sess.observations_frame(last_s=DT * 5)) <= 6


def test_missing_forecast_is_not_zero_risk(tmp_path):
    eng, s, recs, sess = session(tmp_path)
    sess.start(); sess.advance(max_observations=3)                        # still warming up
    st = eng.get_twin("1")
    assert eng.latest_prediction("1") is None and st.forecast.status == "none"
