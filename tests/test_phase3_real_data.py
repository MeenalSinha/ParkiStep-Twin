"""REAL FoG-STAR data + REAL trained Phase 2 artifacts (no synthetic data, no test doubles).

These tests need data/processed (Phase 1) and artifacts/phase2. If either is missing they SKIP with the reason, unless
PARKISTEP_REQUIRE_REAL=1 is set, in which case a missing prerequisite is a FAILURE.
The saved models were fitted on all participants, so replays here are in-sample software checks, not performance evidence."""
import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from p3helpers import REPO, require_real
from parkistep.forecast.inference import ForecastPredictor
from parkistep.forecast.prepare import dataset_columns
from parkistep.twin import cli
from parkistep.twin.config import load_phase3
from parkistep.twin.engine import TwinEngine, observation_clock
from parkistep.twin.errors import TwinError
from parkistep.twin.replay import ReplayRunner
from parkistep.twin.service import TwinService, build_engine
from parkistep.twin.streams import (DataFrameStream, GroundTruth, build_replay_stream, load_processed_tables, select_recordings, stream_columns)

# Regression anchors: facts about the pinned FoG-STAR download established by the Phase 1 audit and independently re-derived from
# the raw CSVs during the Phase 3 verification (docs/phase3/PHASE2_VERIFICATION.md).
N_RECORDINGS, N_PARTICIPANTS, N_EVENTS, N_SENSOR_ROWS = 106, 22, 101, 329027


@pytest.fixture(scope="module")
def real():
    base = load_phase3(REPO / "configs/phase3.yaml", REPO)
    require_real((base.phase2.artifact_dir / "models").is_dir(), f"trained Phase 2 artifacts not found in {base.phase2.artifact_dir}")
    try:
        sensor, clin = load_processed_tables(base)
    except TwinError as e:
        require_real(False, str(e))
    return base, sensor, clin


def settings_for(tmp_path, *extra):
    return load_phase3(REPO / "configs/phase3.yaml", REPO, [f"persistence.path={tmp_path}/state.sqlite", f"outputs.dir={tmp_path}/out", *extra])


def engine_for(s, persistence=False, clock=observation_clock):
    return build_engine(s, persistence=persistence, clock=clock)


def replay_once(s, sensor, tmp_dir_unused=None, patient=None, permute_labels=False):
    eng = engine_for(s)
    recs = select_recordings(sensor, s)
    df = sensor
    if permute_labels:
        df = sensor.copy()
        cols = dataset_columns(s.phase2)
        rng = np.random.default_rng(0)
        df[cols["label"]] = rng.permutation(df[cols["label"]].to_numpy())
    stream, truth = build_replay_stream(df, s, recs, eng.handle.requirements.raw_channels)
    pid = stream.describe()["participants"][0]
    eng.create_twin(pid)
    summary = ReplayRunner(s, eng).run(pid, stream, truth)
    return eng, pid, stream, truth, summary


# ------------------------------------------------------------------ gate: dataset and artifacts
def test_processed_real_data_matches_the_verified_audit(real):
    base, sensor, clin = real
    cols = dataset_columns(base.phase2)
    man = json.loads((base.phase2.base.processed_dir / base.phase2.base.project["outputs"]["manifest"]).read_text())
    assert man["audit_status"] in ("PASS", "PASS_WITH_WARNINGS") and not man.get("synthetic_test_data")
    assert len(sensor) == N_SENSOR_ROWS and sensor[cols["recording"]].nunique() == N_RECORDINGS
    assert sensor[cols["participant"]].nunique() == N_PARTICIPANTS and len(clin) == N_PARTICIPANTS
    ev = GroundTruth.from_sensor(sensor, base, list(dict.fromkeys(sensor[cols["recording"]]))).events
    assert len(ev) == N_EVENTS
    prov = json.loads((REPO / "data/raw/fogstar/PROVENANCE.json").read_text()) if (REPO / "data/raw/fogstar/PROVENANCE.json").exists() else None
    if prov:                                                      # raw files still match the recorded checksums
        for name, meta in (prov.get("files") or {}).items():
            f = REPO / "data/raw/fogstar" / name
            if f.exists() and meta.get("sha256"):
                assert hashlib.sha256(f.read_bytes()).hexdigest() == meta["sha256"], name


def test_every_saved_model_artifact_exists_matches_its_checksum_and_loads(real):
    base, *_ = real
    mdir = base.phase2.artifact_dir / "models"
    manifests = sorted(mdir.glob("*.manifest.json"))
    assert len(manifests) >= 12
    for mp in manifests:
        m = json.loads(mp.read_text())
        art = mdir / m["artifact"]
        assert art.is_file(), f"manifest {mp.name} has no model file"
        assert hashlib.sha256(art.read_bytes()).hexdigest() == m["sha256"], mp.name
        assert m["config_hash"] == base.phase2.config_hash and m["experiment_id"] == base.phase2.experiment_id
        ForecastPredictor.load(mdir, mp.name[:-len(".manifest.json")])


def test_configured_model_loads_and_predicts_on_a_real_trailing_window(real, tmp_path):
    base, sensor, clin = real
    s = settings_for(tmp_path)
    eng = engine_for(s)
    h = eng.handle
    assert h.available and h.horizon_s == s.cfg["model"]["horizon_s"] and h.threshold.source == "model_manifest"
    cols = dataset_columns(s.phase2)
    rid = select_recordings(sensor, s)[0]
    g = sensor[sensor[cols["recording"]] == rid].reset_index(drop=True)
    n = h.requirements.n_samples
    win = g.iloc[1000 - n:1000][[h.requirements.timestamp_col, *h.requirements.raw_channels]]
    r = h.predictor.predict(win, h.requirements.timestamp_col)
    assert r.data_quality == "ok" and 0 <= r.probability <= 1 and r.prediction_time == win.timestamp_s.iloc[-1]
    assert r.threshold == h.threshold.value and r.horizon_s == h.horizon_s
    assert r.window_start_time == win.timestamp_s.iloc[0] and win.timestamp_s.iloc[-1] < g.timestamp_s.iloc[1000]      # no later sample used


# ------------------------------------------------------------------ replay
def test_replay_stream_exposes_no_annotation_columns(real, tmp_path):
    base, sensor, _ = real
    s = settings_for(tmp_path)
    eng = engine_for(s)
    stream, truth = build_replay_stream(sensor, s, select_recordings(sensor, s), eng.handle.requirements.raw_channels)
    ann = {"fog", "fog_severity", "activity", "task_id", "session_id"}
    first = next(iter(stream.records()))
    assert not (set(first) & ann), set(first) & ann
    assert set(first) == set(stream_columns(s, eng.handle.requirements.raw_channels)) == set(stream.describe()["fields"])
    assert len(truth.events) > 0 and truth.bounds


def test_replay_is_sequential_bounded_reproducible_and_future_free(real, tmp_path):
    base, sensor, clin = real
    s = settings_for(tmp_path, "replay.max_observations=2600")
    e1, pid, stream, truth, a = replay_once(s, sensor)
    e2, _, _, _, b = replay_once(s, sensor)
    c = a["counts"]
    n_win = e1.handle.requirements.n_samples
    steps = int(round(e1.cadence / e1.handle.requirements.dt_s))
    assert c["observations_offered"] == c["observations_processed"] == 2600 and not c["rejected"]
    assert c["predictions"] == (2600 - n_win) // steps + 1                          # forecasts only once the window is full, then every cadence
    assert a["future_data_check"]["violations"] == 0 and a["future_data_check"]["predictions_checked"] == c["predictions"]
    assert a["determinism_digest"] == b["determinism_digest"]                       # same data + same configuration => identical forecasts
    pa = [(p.prediction_id, p.probability) for p in e1.recent_predictions(pid, 9999)]
    assert pa == [(p.prediction_id, p.probability) for p in e2.recent_predictions(pid, 9999)]
    assert a["observations_per_status"].get("WARMING_UP") == n_win - 1 and a["observations_per_status"].get("READY", 0) > 2000
    first = e1.recent_predictions(pid, 9999)[0]
    assert first.window_start_time == pytest.approx(stream.df.iloc[0][s.cfg["observation"]["timestamp"]["field"]])   # window starts at recording start
    assert len(e1._rt(pid).buffer) <= e1._rt(pid).buffer.maxlen < 2600                # memory stayed bounded during the replay
    assert a["in_sample_note"] and "not evidence of predictive performance" in a["in_sample_note"]


def test_forecasts_do_not_depend_on_the_annotation_labels(real, tmp_path):
    base, sensor, _ = real
    s = settings_for(tmp_path, "replay.max_observations=1500")
    _, _, _, _, a = replay_once(s, sensor)
    _, _, _, _, b = replay_once(s, sensor, permute_labels=True)                     # labels shuffled before the stream is built
    assert a["determinism_digest"] == b["determinism_digest"]


def test_engine_forecasts_equal_direct_predictor_calls_on_real_windows(real, tmp_path):
    base, sensor, _ = real
    s = settings_for(tmp_path, "replay.max_observations=1800")
    eng, pid, stream, truth, summary = replay_once(s, sensor)
    h = eng.handle
    cols = dataset_columns(s.phase2)
    rid = select_recordings(sensor, s)[0]
    g = sensor[sensor[cols["recording"]] == rid].reset_index(drop=True)
    n = h.requirements.n_samples
    preds = eng.recent_predictions(pid, 9999)
    for p in preds[::9]:
        end = int(np.argmin(np.abs(g[cols["time"]].to_numpy() - p.window_end_time)))
        win = g.iloc[end - n + 1:end + 1][[cols["time"], *h.requirements.raw_channels]]
        d = h.predictor.predict(win, cols["time"])
        assert d.probability == pytest.approx(p.probability, abs=1e-12) and p.threshold == d.threshold and p.above_threshold == d.above_threshold


def test_recording_boundary_on_real_data_never_mixes_recordings(real, tmp_path):
    base, sensor, _ = real
    s = settings_for(tmp_path)
    cols = dataset_columns(s.phase2)
    rc, pc, tc = cols["recording"], cols["participant"], cols["time"]
    order = list(dict.fromkeys(sensor[rc]))
    pairs = [(a, b) for a, b in zip(order, order[1:]) if sensor.loc[sensor[rc] == a, pc].iloc[0] == sensor.loc[sensor[rc] == b, pc].iloc[0]]
    assert pairs, "dataset has no consecutive recordings of one participant"
    a_id, b_id = pairs[0]
    A, B = sensor[sensor[rc] == a_id].iloc[-400:], sensor[sensor[rc] == b_id].iloc[:500]
    dt = sensor[tc].diff().median()
    assert B[tc].iloc[0] - A[tc].iloc[-1] == pytest.approx(dt, rel=0.05), "source timestamps are expected to be continuous across the boundary"
    eng = engine_for(s)
    both = pd.concat([A, B])
    pid = str(both[pc].iloc[0])
    eng.create_twin(pid)
    stream = DataFrameStream(both, stream_columns(s, eng.handle.requirements.raw_channels))
    ends = {}
    for r in stream.records():
        res = eng.ingest(pid, r)
        for u in res.updates:
            if u.prediction is not None:
                ends.setdefault(u.prediction.session_id, []).append((u.prediction.window_start_time, u.prediction.window_end_time))
    assert set(ends) == {a_id, b_id}
    b_first = B[tc].iloc[0]
    assert all(e <= A[tc].iloc[-1] + 1e-9 for _, e in ends[a_id])
    assert all(st >= b_first - 1e-9 for st, _ in ends[b_id]), "a window in recording B contains samples from recording A"
    n = eng.handle.requirements.n_samples
    assert min(e for _, e in ends[b_id]) >= b_first + (n - 1) * dt * 0.99                        # B needs its own full window first
    kinds = [e.kind for e in eng.event_history(pid)]
    assert kinds.count("SESSION_START") == 2 and "SESSION_END" in kinds and eng.get_twin(pid).stream.session_id == b_id


def test_full_recording_replay_summary_and_retrospective_accounting(real, tmp_path):
    base, sensor, clin = real
    s = settings_for(tmp_path)
    eng, pid, stream, truth, summ = replay_once(s, sensor)
    c, rc = summ["counts"], summ["retrospective_comparison"]
    assert c["observations_offered"] == stream.describe()["n_records"] == c["observations_processed"] and not c["rejected"]
    assert c["warnings_activated"] == len(eng.warning_history(pid)) == sum(c["warnings_by_status"].values())
    assert c["predictions"] == eng.get_twin(pid).forecast.n_predictions == len(eng.recent_predictions(pid, 10 ** 9)) > 100
    assert summ["future_data_check"]["violations"] == 0 and summ["final_state"]["stream"]["session_id"] is None
    t = rc["totals"]
    assert rc["labels_used_only_after_replay"] is True and t["annotated_events"] == len(truth.events) > 0
    pol = eng.handle.alert_policy.get("extra_alerts_for_matched_event", "ignore")
    false_wo_red = t["false_warnings"] - (t["redundant_warnings"] if pol == "false" else 0)
    scored = t["valid_early_warnings"] + t["redundant_warnings"] + false_wo_red
    assert scored + t["warnings_excluded_overlap_freezing"] + t["warnings_excluded_censored_future"] == c["warnings_activated"]
    assert t["events_detected"] == t["valid_early_warnings"] and 0 <= t["events_detected"] <= t["annotated_events"]
    assert "in-sample" in rc["note"] and summ["model_training_scope"]
    assert all(e["detected"] == (e["warning_lead_s"] is not None) for e in rc["events"])
    assert all(0 < e["warning_lead_s"] <= eng.handle.horizon_s + 1e-6 for e in rc["events"] if e["detected"])


def test_service_view_of_a_real_twin_separates_score_policy_and_quality(real, tmp_path):
    base, sensor, clin = real
    s = settings_for(tmp_path, "replay.max_observations=1500")
    eng, pid, *_ = replay_once(s, sensor)
    eng2 = engine_for(s)                                   # fresh twin mid-recording to look at a live (non-finished) view
    recs = select_recordings(sensor, s)
    stream, _ = build_replay_stream(sensor, s, recs, eng2.handle.requirements.raw_channels)
    eng2.create_twin(pid)
    for i, r in enumerate(stream.records()):
        eng2.ingest(pid, r)
        if i == 900:
            break
    v = TwinService(eng2).view(pid)
    assert v["twin"]["status"] == "READY" and v["model_score"]["is_current"] and v["model_score"]["current_forecast"]["horizon_s"] == eng2.handle.horizon_s
    assert v["clinical_context"]["status"] in ("valid", "partial") and v["clinical_context"]["provenance"]["source"] == "phase1_processed"
    assert v["clinical_context"]["influences_forecast"] == eng2.handle.uses_clinical
    json.dumps(v)


# ------------------------------------------------------------------ command line, end to end
def test_cli_create_replay_inspect_roundtrip(real, tmp_path, capsys):
    base, sensor, _ = real
    ov = ["--set", f"persistence.path={tmp_path}/state.sqlite", "--set", f"outputs.dir={tmp_path}/out", "--set", "replay.max_observations=1500"]
    assert cli.main("verify-model", ov) == 0
    assert cli.main("replay", ov) == 0
    out = capsys.readouterr().out
    summary_file = next((tmp_path / "out").glob("replay_*_summary.json"))
    summ = json.loads(summary_file.read_text())
    assert summ["counts"]["observations_offered"] == 1500 and summ["future_data_check"]["violations"] == 0 and summ["determinism_digest"] in out
    for kind in ("predictions.csv", "warnings.json", "events.json", "final_state.json"):
        assert next((tmp_path / "out").glob(f"replay_*_{kind}")).exists()
    df = pd.read_csv(next((tmp_path / "out").glob("replay_*_predictions.csv")))
    assert len(df) == summ["counts"]["predictions"] and df.probability.between(0, 1).all() and df.prediction_id.is_unique
    capsys.readouterr()
    assert cli.main("inspect", ov) == 0
    view = json.loads(capsys.readouterr().out)
    assert view["twin"]["patient_id"] == summ["patient_id"] and view["twin"]["restored"]["prior_update_seq"] >= 1500
    assert view["twin"]["status"] == "WAITING_FOR_DATA" and view["model_score"]["forecast_status"] == "historical"
    assert view["model_score"]["is_current"] is False and view["model_score"]["current_forecast"] is None
    assert view["model_score"]["n_forecasts"] == summ["counts"]["predictions"]
    assert view["warning_policy"]["n_warnings"] == summ["counts"]["warnings_activated"]
    assert cli.main("inspect", ov + ["--events", "STATUS_CHANGE"]) == 0
    assert any(e["kind"] == "STATUS_CHANGE" for e in json.loads(capsys.readouterr().out)["persisted_events"])
    assert cli.main("inspect", ov + ["--list"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert len(listed) == 1 and listed[0]["patient_id"] == summ["patient_id"]
    # replaying again must not silently overwrite the persisted twin
    assert cli.main("replay", ov) == 1
    assert "already exists" in capsys.readouterr().err
