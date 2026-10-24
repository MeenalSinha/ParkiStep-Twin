"""Replay driver and retrospective comparison with KNOWN answers (SYNTHETIC data, scripted predictor test double)."""
import numpy as np
import pandas as pd
import pytest

from p3helpers import DT, channels, make_engine, rec, stream
from parkistep.twin.engine import observation_clock, wall_clock
from parkistep.twin.errors import TwinError
from parkistep.twin.replay import ReplayRunner, write_replay_outputs
from parkistep.twin.streams import DataFrameStream, GroundTruth, SensorStream


class ListStream(SensorStream):
    def __init__(self, records, fields=None):
        self.r, self.fields = records, fields

    def records(self):
        return iter(self.r)

    def describe(self):
        return {"kind": "test", "fields": self.fields if self.fields is not None else sorted(self.r[0]), "n_records": len(self.r)}


def run_replay(tmp_path, score, n=300, *overrides, truth=None, clock=observation_clock, sleep=None):
    eng, stub, s = make_engine(tmp_path, *overrides, score=score, clock=clock)
    eng.create_twin(1)
    runner = ReplayRunner(s, eng, sleep=sleep) if sleep else ReplayRunner(s, eng)
    return eng, s, runner.run("1", ListStream(stream(s, n)), truth)


def test_speed_zero_never_sleeps_and_speed_scales_the_delays(tmp_path):
    slept = []
    run_replay(tmp_path / "a", 0.1, 50, "replay.speed=0", sleep=slept.append)
    assert slept == []
    slept2 = []
    run_replay(tmp_path / "b", 0.1, 50, "replay.speed=4", sleep=slept2.append)
    assert len(slept2) == 49 and sum(slept2) == pytest.approx(49 * DT / 4) and max(slept2) == pytest.approx(DT / 4)


def test_summary_reports_actual_counts_and_the_future_data_check(tmp_path):
    eng, s, summ = run_replay(tmp_path, lambda t: 0.9 if 3 <= t < 4 else 0.1, 200)
    c = summ["counts"]
    assert c["observations_offered"] == c["observations_processed"] == 200 and c["predictions"] == len(eng.recent_predictions("1", 999))
    assert c["predictions"] == (200 - 11) // 2 + 1 and c["warnings_activated"] == len(eng.warning_history("1")) >= 1
    assert summ["future_data_check"] == {"predictions_checked": c["predictions"], "violations": 0, "rule": summ["future_data_check"]["rule"]}
    assert summ["observations_per_status"]["WARMING_UP"] == 10 and summ["observations_per_status"]["READY"] == 190
    assert summ["final_state"]["stream"]["session_id"] is None and "not evidence of predictive performance" in summ["in_sample_note"]
    assert summ["threshold"]["source"] == "model_manifest" and summ["model"]["horizon_s"] == 3.0


def test_stream_records_outside_the_declared_fields_are_refused(tmp_path):
    eng, stub, s = make_engine(tmp_path)
    eng.create_twin(1)
    recs = stream(s, 20)
    declared = sorted(recs[0])
    recs[5] = {**recs[5], "fog": 1}                                        # an annotation sneaking into the live stream
    with pytest.raises(TwinError, match="outside the declared set"):
        ReplayRunner(s, eng).run("1", ListStream(recs, declared), None)


def test_digest_ignores_wall_clock_fields_but_reacts_to_content(tmp_path):
    sc = lambda t: 0.9 if 3 <= t < 4 else 0.1
    _, _, a = run_replay(tmp_path / "a", sc, 150, clock=observation_clock)
    _, _, b = run_replay(tmp_path / "b", sc, 150, clock=wall_clock)
    assert a["determinism_digest"] == b["determinism_digest"]
    _, _, c = run_replay(tmp_path / "c", lambda t: 0.9 if 3 <= t < 4.2 else 0.1, 150)
    assert c["determinism_digest"] != a["determinism_digest"]


def test_retrospective_comparison_classifies_warnings_correctly(tmp_path):
    """Recording r1 spans t=0..29.9. Known answers (horizon 3 s, window 1 s):
       burst at 9.5-10.5   -> warning 9.6, onset 12.0 is 2.4 s ahead         => valid early warning (event e0 detected)
       burst at 20.0-20.4  -> no onset within 3 s                             => false warning
       burst at 25.5-25.9  -> inside annotated episode e1 [24, 26]            => excluded (input overlaps freezing)
       burst at 28.9-29.4  -> horizon would run past the recording end (29.9) => excluded (censored future)
       event e2 at 17.0 has no warning before it                              => missed"""
    def score(t):
        return 0.9 if (9.5 <= t <= 10.5 or 20.0 <= t <= 20.4 or 25.5 <= t <= 25.9 or 28.9 <= t <= 29.4) else 0.1
    ev = pd.DataFrame({"event_id": ["e0", "e2", "e1"], "recording_id": ["r1"] * 3, "onset_t": [12.0, 17.0, 24.0], "end_t": [12.5, 17.5, 26.0]})
    truth = GroundTruth(ev, ["r1"], {"r1": (0.0, 29.9)})
    eng, s, summ = run_replay(tmp_path, score, 300, truth=truth)
    assert summ["counts"]["warnings_activated"] == 4
    t = summ["retrospective_comparison"]["totals"]
    assert t["annotated_events"] == 3 and t["events_detected"] == 1 and t["valid_early_warnings"] == 1
    assert t["false_warnings"] == 1 and t["warnings_excluded_overlap_freezing"] == 1 and t["warnings_excluded_censored_future"] == 1
    det = {e["event_id"]: e for e in summ["retrospective_comparison"]["events"]}
    assert det["e0"]["detected"] and det["e0"]["warning_lead_s"] == pytest.approx(2.4) and not det["e1"]["detected"] and not det["e2"]["detected"]
    assert summ["retrospective_comparison"]["labels_used_only_after_replay"] is True and t["event_sensitivity_all_events"] == pytest.approx(1 / 3)


def test_labels_cannot_change_the_forecasts_or_warnings(tmp_path):
    sc = lambda t: 0.9 if 9.5 <= t <= 10.5 else 0.1
    ev1 = pd.DataFrame({"event_id": ["e0"], "recording_id": ["r1"], "onset_t": [12.0], "end_t": [12.5]})
    ev2 = pd.DataFrame({"event_id": ["x"], "recording_id": ["r1"], "onset_t": [3.0], "end_t": [29.0]})
    _, _, a = run_replay(tmp_path / "a", sc, 300, truth=GroundTruth(ev1, ["r1"], {"r1": (0.0, 29.9)}))
    _, _, b = run_replay(tmp_path / "b", sc, 300, truth=GroundTruth(ev2, ["r1"], {"r1": (0.0, 29.9)}))
    assert a["determinism_digest"] == b["determinism_digest"]            # forecasts/warnings identical; only the after-the-fact comparison differs
    assert a["retrospective_comparison"]["totals"] != b["retrospective_comparison"]["totals"]


def test_replay_outputs_respect_the_overwrite_policy(tmp_path):
    eng, s, summ = run_replay(tmp_path, 0.1, 60)
    paths = write_replay_outputs(s, eng, summ, "t")
    assert all(__import__("pathlib").Path(p).exists() for p in paths.values())
    df = pd.read_csv(paths["predictions"])
    assert len(df) == summ["counts"]["predictions"] and df.prediction_id.is_unique
    write_replay_outputs(s, eng, summ, "t")                                # outputs.overwrite=true (default) replaces them
    eng2, s2, summ2 = run_replay(tmp_path / "x", 0.1, 60, "outputs.overwrite=false", f"outputs.dir={tmp_path}/o2")
    write_replay_outputs(s2, eng2, summ2, "t")
    with pytest.raises(TwinError, match="outputs.overwrite is false"):
        write_replay_outputs(s2, eng2, summ2, "t")


def test_dataframe_stream_exposes_only_allowed_columns_and_honours_the_cap():
    df = pd.DataFrame({"a": range(10), "b": range(10), "fog": [1] * 10})
    st = DataFrameStream(df, ["a", "b"], max_rows=4)
    rows = list(st.records())
    assert len(rows) == 4 and all(set(r) == {"a", "b"} for r in rows) and st.describe()["n_records"] == 4
    with pytest.raises(TwinError, match="lacks required columns"):
        DataFrameStream(df, ["a", "zzz"])
