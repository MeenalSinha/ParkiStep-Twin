import numpy as np
import pandas as pd
import pytest

from p2helpers import COLS, WIN, rec_frame, shared_clock_sensor
from parkistep.forecast.events import extract_events
from parkistep.forecast.windows import build_anchor_table, expected_samples, horizon_key, summarize_targets


def _events(df, **kw):
    return extract_events(df, recording_col="recording_id", participant_col="participant_id", session_col="session_id",
                          task_col="task_id", time_col="timestamp_s", label_col="fog", positive_value=1,
                          severity_col="fog_severity", **kw)


def test_events_extracted_from_runs_not_merged_across_recordings():
    df = shared_clock_sensor()
    ev = _events(df)
    assert len(ev) == 3 and set(ev.recording_id) == {"r1", "r2"}
    e0 = ev.iloc[0]
    r1 = df[df.recording_id == "r1"]
    assert e0.onset_t == pytest.approx(r1.timestamp_s.iloc[30]) and e0.end_t == pytest.approx(r1.timestamp_s.iloc[39])
    assert e0.duration_s == pytest.approx(1.0)
    assert ev.event_id.is_unique


def test_event_running_to_recording_end_does_not_continue_into_next_recording():
    a = rec_frame("a", 1, 50, 0.1, 0.0, [(40, 50)]); b = rec_frame("b", 1, 50, 0.1, 5.0, [(0, 5)], task=2)
    ev = _events(pd.concat([a, b], ignore_index=True))
    assert len(ev) == 2 and ev.touches_recording_end.tolist() == [True, False]


def test_merge_gap_is_configurable_and_off_by_default():
    df = rec_frame("a", 1, 100, 0.1, 0.0, [(20, 30), (32, 40)])
    assert len(_events(df)) == 2
    assert len(_events(df, merge_gap_s=0.5)) == 1
    assert len(_events(df, min_duration_s=1.0)) == 1
    with pytest.raises(ValueError):
        _events(df, merge_gap_s=-1)


def _anchors(df, horizons=(0.5, 1.0), **win):
    ev = _events(df)
    w = {**WIN, **win}
    return build_anchor_table(df, ev, cols=COLS, windowing=w, horizons=list(horizons)), ev


def test_labels_follow_future_onset_definition():
    df = shared_clock_sensor()
    a, ev = _anchors(df)
    for H in (0.5, 1.0):
        k = horizon_key(H)
        el = a[a[f"{k}_status"] == "eligible"]
        assert len(el) > 0
        for r in el.itertuples():
            on = ev[ev.recording_id == r.recording_id].onset_t.to_numpy()
            expect = int(((on > r.anchor_t) & (on <= r.anchor_t + H + 1e-9)).any())
            assert getattr(r, f"{k}_y") == expect


def test_input_window_never_contains_event_or_future_and_stays_in_recording():
    df = shared_clock_sensor()
    a, ev = _anchors(df)
    el = a[a.base_status == "eligible_base"]
    for r in el.itertuples():
        g = df[df.recording_id == r.recording_id].reset_index(drop=True)
        lo = r.anchor_idx - r.n_window_samples + 1
        assert lo >= 0 and r.anchor_idx < len(g)                          # inside one recording
        assert g.fog.iloc[lo:r.anchor_idx + 1].sum() == 0                  # no ongoing/previous event in the input window
        assert r.window_start_t == pytest.approx(g.timestamp_s.iloc[lo])
        assert r.window_start_t >= g.timestamp_s.iloc[0] - 1e-9
    assert (a[a.base_status == "insufficient_history"].anchor_idx < expected_samples(1.0, 0.1) - 1).all()


def test_ongoing_event_anchors_excluded():
    df = shared_clock_sensor()
    a, _ = _anchors(df)
    r1 = a[a.recording_id == "r1"]
    inside = r1[(r1.anchor_idx >= 30) & (r1.anchor_idx < 40)]
    assert (inside.base_status == "ongoing_event_at_anchor").all()
    after = r1[(r1.anchor_idx >= 40) & (r1.anchor_idx < 40 + 10)]
    assert (after.base_status != "eligible_base").all()                    # window still overlaps the event


def test_censored_future_not_labelled_negative():
    df = shared_clock_sensor()
    a, _ = _anchors(df, horizons=(2.0,))
    k = horizon_key(2.0)
    r3 = a[a.recording_id == "r3"]
    end = df[df.recording_id == "r3"].timestamp_s.iloc[-1]
    late = r3[(r3.anchor_t + 2.0 > end + 1e-9) & (r3.base_status == "eligible_base")]
    assert len(late) > 0 and (late[f"{k}_status"] == "censored_future").all()


def test_boundary_between_abutting_recordings_is_respected():
    # the clock is continuous across r1->r2, yet r2's first anchors must not reuse r1 samples
    df = shared_clock_sensor()
    a, _ = _anchors(df)
    r2 = a[a.recording_id == "r2"]
    n = int(r2.n_window_samples.iloc[0])
    assert (r2[r2.anchor_idx < n - 1].base_status == "insufficient_history").all()
    # and a positive for r1's future can never come from r2's event
    k = horizon_key(1.0)
    r1_end = df[df.recording_id == "r1"].timestamp_s.iloc[-1]
    pos = a[(a.recording_id == "r1") & (a[f"{k}_y"] == 1)]
    assert (pos[f"{k}_event_id"].str.startswith("r1")).all() and (pos.anchor_t < r1_end).all()


def test_irregular_timestamps_exclude_window():
    df = shared_clock_sensor()
    df.loc[(df.recording_id == "r3") & (df.index > df.index[df.recording_id == "r3"][40]), "timestamp_s"] += 0.5   # gap
    a, _ = _anchors(df)
    r3 = a[a.recording_id == "r3"]
    assert (r3.base_status == "timestamp_irregular_window").any()


def test_configurable_window_and_stride():
    df = shared_clock_sensor()
    a1, _ = _anchors(df, input_window_s=1.0, stride_s=0.2)
    a2, _ = _anchors(df, input_window_s=0.5, stride_s=0.4)
    assert a1.n_window_samples.iloc[0] == 11 and a2.n_window_samples.iloc[0] == 6
    assert len(a2) < len(a1)


def test_task_restriction_is_configurable():
    df = shared_clock_sensor()
    ev = _events(df)
    a = build_anchor_table(df, ev, cols=COLS, windowing=WIN, horizons=[0.5], eligible_tasks=[2])
    assert set(a[a.base_status == "eligible_base"].recording_id) == {"r2"}


def test_summary_counts_are_consistent():
    df = shared_clock_sensor()
    a, ev = _anchors(df)
    s = summarize_targets(a, ev, [0.5, 1.0], 0.2)
    h = s["horizons"][horizon_key(1.0)]
    assert h["n_positive"] + h["n_negative"] == h["n_eligible"]
    assert h["n_events_with_positive_anchor"] + h["n_events_without_positive_anchor"] == len(ev)
