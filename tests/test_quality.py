import numpy as np

from helpers import make_workspace
from parkistep.pipeline import run_audit


def test_missing_values_counted(tmp_path):
    def ed(d):
        d = d.copy(); d.loc[0:9, "ankleL_acc_x"] = np.nan; d.loc[0:4, "back_gyro_z"] = np.nan; return d
    r = run_audit(make_workspace(tmp_path, sensor_edit=ed))
    m = r.report["sensor"]["missing"]
    assert m["per_channel"]["ankleL_acc_x"]["missing"] == 10
    assert m["total_missing_cells"] == 15 and m["rows_with_any_missing"] == 10
    assert "SENSOR_MISSING_VALUES" in {i.code for i in r.issues.warnings}


def test_duplicate_timestamps_detected(tmp_path):
    r = run_audit(make_workspace(tmp_path, sensor_edit=lambda d: d.assign(timestamp=d.timestamp.where(d.index != 11, d.timestamp.shift(1)))))
    assert r.report["sensor"]["sampling"]["duplicate_timestamps"] == 1 and r.status == "FAIL"


def test_ordering_respects_recording_boundaries(tmp_path):
    # fixture timestamps reset to 0 for each recording: must NOT be flagged
    ok = run_audit(make_workspace(tmp_path / "a"))
    assert ok.report["sensor"]["sampling"]["out_of_order_steps"] == 0
    # a backwards step inside one recording must be flagged
    def ed(d):
        d = d.copy(); d.loc[50, "timestamp"] = d.loc[40, "timestamp"] - 0.05; return d
    bad = run_audit(make_workspace(tmp_path / "b", sensor_edit=ed))
    assert bad.report["sensor"]["sampling"]["out_of_order_steps"] >= 1 and bad.status == "FAIL"


def test_sampling_frequency_estimation(tmp_path):
    r = run_audit(make_workspace(tmp_path, hz=25.0))
    assert abs(r.report["sensor"]["sampling"]["estimated_hz_overall"] - 25.0) < 1e-6
    assert r.report["sensor"]["n_recordings"] == 6


def test_gap_detection_uses_configurable_threshold(tmp_path):
    def ed(d):
        d = d.copy(); d.loc[60:, "timestamp"] += 0.25      # gap of ~0.35s in recording 0 (interval 0.1s)
        return d.loc[:99].combine_first(d) if False else d
    def sensor_edit(d):
        d = d.copy(); idx = d.index[(d.index >= 60) & (d.index < 100)]; d.loc[idx, "timestamp"] += 0.25; return d
    r = run_audit(make_workspace(tmp_path / "a", sensor_edit=sensor_edit))
    assert r.report["sensor"]["sampling"]["gaps"]["count"] == 1
    r2 = run_audit(make_workspace(tmp_path / "b", sensor_edit=sensor_edit,
                                  profile_edit=lambda ds: ds["sampling"].update(gap_threshold_factor=10)))
    assert r2.report["sensor"]["sampling"]["gaps"]["count"] == 0


def test_episode_detection_and_duration(tmp_path):
    r = run_audit(make_workspace(tmp_path))
    e = r.report["annotation"]["episodes"]
    assert e["count"] == 2 and e["participants_with_episodes"] == 2
    assert abs(e["duration_s"]["mean"] - 1.0) < 1e-6      # 10 samples at 10 Hz
    assert r.report["annotation"]["fields"]["fog"]["value_counts"] == {"0": 580, "1": 20}


def test_constant_channel_flagged(tmp_path):
    r = run_audit(make_workspace(tmp_path, sensor_edit=lambda d: d.assign(back_acc_z=1.0)))
    assert r.report["sensor"]["constant_channels"] == ["back_acc_z"]


def test_readiness_requires_episodes(tmp_path):
    r = run_audit(make_workspace(tmp_path, sensor_edit=lambda d: d.assign(fog=0, fog_severity=0)))
    assert r.status == "FAIL" and "READINESS_TOO_FEW_EPISODES" in {i.code for i in r.issues.errors}
