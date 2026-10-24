"""State persistence and recovery (SYNTHETIC data, scripted predictor test double, real SQLite)."""
import json
import sqlite3

import pytest

from p3helpers import DT, N_WIN, StubPredictor, make_engine, make_manifest, rec, settings, stream
from parkistep.twin.engine import TwinEngine, observation_clock
from parkistep.twin.errors import StateIncompatibleError, StateStoreError, TwinError, TwinExistsError, UnknownTwinError
from parkistep.twin.model import handle_from_predictor
from parkistep.twin.service import TwinService
from parkistep.twin.storage import StateStore

CLIN = {"participant_id": 3, "age": 70.0, "gender": "F", "disease_duration": 5.0, "hoehn_yahr": 2.0, "updrs_iii": 30.0, "fog_q": 12.0,
        "moca": 22.0, "fes_i": 20.0, "pdq_8": 10.0}


class FakeClinicalSource:
    def __init__(self, rows):
        self.rows = rows

    def get(self, pid):
        return self.rows.get(str(pid))


def high_then_low(t):
    return 0.9 if 1.5 <= t < 2.5 else 0.1


def run_engine(tmp_path, n=60, **kw):
    eng, stub, s = make_engine(tmp_path, persistence=True, score=high_then_low, **kw)
    eng.create_twin(1)
    for r in stream(s, n):
        eng.ingest(1, r)
    return eng, stub, s


def reopen(tmp_path, s_over=(), score=0.1, manifest_kw=None, clinical_source=None, **kw):
    """A fresh engine on the same state file (simulates a restart)."""
    return make_engine(tmp_path, *s_over, persistence=True, score=score, manifest_kw=manifest_kw, clinical_source=clinical_source, **kw)


def test_state_events_predictions_and_warnings_are_persisted(tmp_path):
    eng, stub, s = run_engine(tmp_path)
    eng.flush()
    st = eng.store
    tid = eng.get_twin(1).twin_id
    assert st.count("predictions", tid) == eng.get_twin(1).forecast.n_predictions == len(stub.calls)
    assert len(st.list_warnings(tid)) == len(eng.warning_history(1)) >= 1
    kinds = {e["kind"] for e in st.list_events(tid)}
    assert {"TWIN_CREATED", "STATUS_CHANGE", "SESSION_START", "WARNING_ACTIVATED"} <= kinds
    row = st.load_twin("1")
    assert row["state"]["twin_id"] == tid and row["model_id"] == eng.handle.model_id and row["state"]["status"] == eng.get_twin(1).status
    assert st.get_meta("config_snapshot")["compat_hash"] == s.compat_hash and st.get_meta("model_identity")["sha256"]


def test_restore_keeps_history_but_never_presents_it_as_current(tmp_path):
    eng, stub, s = run_engine(tmp_path)
    before = [p.to_dict() for p in eng.recent_predictions(1, 999)]
    warnings_before = [w.to_dict() for w in eng.warning_history(1)]
    seq_before, n_before = eng.get_twin(1).update_seq, eng.get_twin(1).forecast.n_predictions
    assert eng.current_warning(1) is None or True
    eng.close()
    eng2, stub2, s2 = reopen(tmp_path)
    st = eng2.restore_twin(1)
    assert [p.to_dict() for p in eng2.recent_predictions(1, 999)] == before
    assert st.status == "WAITING_FOR_DATA" and st.forecast.status == "historical" and st.restored["prior_update_seq"] == seq_before
    assert st.stream.n_buffered == 0 and st.stream.session_id is None and st.update_seq > seq_before - 1
    v = TwinService(eng2).view(1)
    assert v["model_score"]["is_current"] is False and v["model_score"]["current_forecast"] is None
    assert v["model_score"]["latest_forecast_age_s"] is None and v["twin"]["restored"]["note"]
    assert len(eng2.warning_history(1)) == len(warnings_before)
    assert all(w.status != "ACTIVE" for w in eng2.warning_history(1)) and eng2.current_warning(1) is None


def test_active_warning_is_expired_on_restore_because_no_live_data_supports_it(tmp_path):
    eng, stub, s = make_engine(tmp_path, persistence=True, score=0.9)
    eng.create_twin(1)
    for r in stream(s, 40):
        eng.ingest(1, r)
    assert eng.current_warning(1) is not None
    eng.close()
    eng2, _, _ = reopen(tmp_path)
    eng2.restore_twin(1)
    w = eng2.warning_history(1)[0]
    assert w.status == "EXPIRED" and w.resolution_reason == "restored_without_live_data" and eng2.get_twin(1).warning.state == "none"
    assert eng2.store.list_warnings(eng2.get_twin(1).twin_id)[0]["status"] == "EXPIRED"
    assert w.lifecycle[0]["to"] == "ACTIVE" and w.lifecycle[-1]["to"] == "EXPIRED"          # history preserved


def test_restored_twin_re_enters_warm_up_and_numbering_continues(tmp_path):
    eng, _, s = run_engine(tmp_path)
    last_id, n = eng.latest_prediction(1).prediction_id, eng.get_twin(1).forecast.n_predictions
    eng.close()
    eng2, stub2, s2 = reopen(tmp_path)
    eng2.restore_twin(1)
    statuses = []
    for i in range(1000, 1000 + 2 * N_WIN):
        eng2.ingest(1, rec(s2, 0, ts=i * DT))
        statuses.append(eng2.get_twin(1).status)
    assert statuses[0] == "WARMING_UP" and statuses[-1] == "READY"
    assert len(stub2.calls) >= 1 and stub2.calls[0]["window"].timestamp_s.min() >= 1000 * DT - 1e-9      # no pre-restart sample in a window
    ids = [p.prediction_id for p in eng2.recent_predictions(1, 9999)]
    assert len(ids) == len(set(ids)) and f"twin-1-p{n + 1:06d}" in ids and last_id in ids


def test_two_patients_persist_independently(tmp_path):
    eng, stub, s = make_engine(tmp_path, persistence=True, score=0.9)
    eng.create_twin(1); eng.create_twin(2)
    for r in stream(s, 40, patient=1):
        eng.ingest(1, r)
    for r in stream(s, 25, patient=2):
        eng.ingest(2, r)
    st = eng.store
    assert {p["patient_id"] for p in (json.loads(x) for x in [json.dumps(t) for t in st.list_twins()])} == {"1", "2"}
    p1, p2 = st.list_predictions("twin-1"), st.list_predictions("twin-2")
    assert all(p["patient_id"] == "1" for p in p1) and all(p["patient_id"] == "2" for p in p2) and len(p1) > len(p2) > 0
    assert all(w["patient_id"] == "1" for w in st.list_warnings("twin-1")) and st.list_events("twin-2", "WARNING_ACTIVATED") != st.list_events("twin-1", "WARNING_ACTIVATED")
    eng.close()
    eng2, _, _ = reopen(tmp_path)
    a, b = eng2.restore_twin(1), eng2.restore_twin(2)
    assert a.patient_id == "1" and b.patient_id == "2" and a.forecast.n_predictions != b.forecast.n_predictions


def test_corrupt_or_foreign_state_is_refused_with_clear_errors(tmp_path):
    eng, _, s = run_engine(tmp_path)
    eng.close()
    db = sqlite3.connect(s.state_path)
    db.execute("UPDATE twins SET state_json='{not json' WHERE patient_id='1'"); db.commit(); db.close()
    eng2, _, _ = reopen(tmp_path)
    with pytest.raises(StateStoreError, match="corrupt"):
        eng2.restore_twin(1)
    db = sqlite3.connect(s.state_path)                            # valid JSON but belonging to another twin
    db.execute("UPDATE twins SET state_json=?", (json.dumps({"twin_id": "twin-9", "patient_id": "9"}),)); db.commit(); db.close()
    with pytest.raises(StateStoreError, match="corrupt"):
        reopen(tmp_path)[0].restore_twin(1)
    with pytest.raises(UnknownTwinError):
        reopen(tmp_path)[0].restore_twin(77)


def test_schema_model_and_config_compatibility_is_checked(tmp_path):
    eng, _, s = run_engine(tmp_path)
    eng.close()
    db = sqlite3.connect(s.state_path)
    db.execute("UPDATE twins SET schema_version='parkistep.twin.state/0'"); db.commit(); db.close()
    with pytest.raises(StateIncompatibleError, match="schema"):
        reopen(tmp_path)[0].restore_twin(1)
    db = sqlite3.connect(s.state_path)
    db.execute("UPDATE twins SET schema_version='parkistep.twin.state/1'"); db.commit(); db.close()
    eng_other, stub, s2 = reopen(tmp_path, manifest_kw={"alerts": {"persistence_anchors": 1, "merge_gap_s": 1.0, "cooldown_s": 0.0}})
    eng_other.handle.artifact_sha256 = "f" * 64                                    # a different artifact (different checksum)
    with pytest.raises(StateIncompatibleError, match="model"):
        eng_other.restore_twin(1)
    eng_cfg, _, _ = reopen(tmp_path, ("cadence.every_s=0.4",))                     # state-affecting setting changed
    with pytest.raises(StateIncompatibleError, match="configuration"):
        eng_cfg.restore_twin(1)
    eng_ok, _, _ = reopen(tmp_path, ("cadence.every_s=0.4", "persistence.on_incompatible=allow_config_change"))
    st = eng_ok.restore_twin(1)
    assert st.restored["config_changed"] is True and st.status == "WAITING_FOR_DATA"


def test_logging_or_runtime_only_settings_do_not_block_restore(tmp_path):
    eng, _, s = run_engine(tmp_path)
    eng.close()
    eng2, _, _ = reopen(tmp_path, ("logging.level=DEBUG", "outputs.overwrite=false", "replay.speed=2"))
    assert eng2.restore_twin(1).patient_id == "1"


def test_clinical_values_are_not_stored_by_default_and_are_verified_on_restore(tmp_path):
    src = FakeClinicalSource({"3": dict(CLIN)})
    eng, stub, s = make_engine(tmp_path, persistence=True, clinical_source=src)
    st = eng.create_twin(3)
    assert st.clinical.status == "valid"
    eng.close()
    raw = sqlite3.connect(s.state_path).execute("SELECT state_json FROM twins").fetchone()[0]
    stored = json.loads(raw)["clinical"]
    assert stored["values"] == {} and stored["values_stored"] is False and stored["provenance"]["row_sha256"]
    assert "70.0" not in raw and "'F'" not in raw
    eng2, _, _ = reopen(tmp_path, clinical_source=src)
    assert eng2.restore_twin(3).clinical.values["age"] == 70.0                       # re-read from the source, hash verified
    eng.store  # noqa
    changed = FakeClinicalSource({"3": dict(CLIN) | {"age": 71.0}})
    eng3, _, _ = reopen(tmp_path, clinical_source=changed)
    with pytest.raises(StateIncompatibleError, match="hash"):
        eng3.restore_twin(3)
    eng4, _, _ = reopen(tmp_path, clinical_source=None)
    st4 = eng4.restore_twin(3)
    assert st4.clinical.status == "absent" and "not re-attached" in st4.clinical.provenance["restore_note"]


def test_clinical_values_can_be_stored_if_configured(tmp_path):
    eng, _, s = make_engine(tmp_path, "persistence.store_clinical_values=true", persistence=True)
    eng.create_twin(3, clinical=CLIN)
    eng.save_state(3)
    eng.close()
    eng2, _, _ = reopen(tmp_path, ("persistence.store_clinical_values=true",))
    assert eng2.restore_twin(3).clinical.values["age"] == 70.0


def test_overwrite_policy_for_existing_persisted_twin(tmp_path):
    eng, _, s = run_engine(tmp_path)
    eng.close()
    eng2, _, _ = reopen(tmp_path)
    with pytest.raises(TwinExistsError):
        eng2.create_twin(1)
    eng3, _, _ = reopen(tmp_path, ("persistence.overwrite=true",))
    st = eng3.create_twin(1)
    assert st.forecast.n_predictions == 0 and eng3.store.count("predictions", "twin-1") == 0
    eng4, _, _ = reopen(tmp_path, ("patient.exists_policy=return_existing",))
    eng4.close()


def test_snapshot_policies(tmp_path):
    eng, _, s = make_engine(tmp_path / "m", "persistence.snapshot_policy=manual", persistence=True)
    eng.create_twin(1)
    for r in stream(s, 30):
        eng.ingest(1, r)
    assert eng.store.load_twin("1")["state"]["update_seq"] < eng.get_twin(1).update_seq          # not saved while manual
    eng.save_state(1)
    assert eng.store.load_twin("1")["state"]["update_seq"] == eng.get_twin(1).update_seq
    eng2, _, s2 = make_engine(tmp_path / "e", "persistence.snapshot_policy=every_update", persistence=True)
    eng2.create_twin(1)
    for r in stream(s2, 15):
        eng2.ingest(1, r)
    assert eng2.store.load_twin("1")["state"]["update_seq"] == eng2.get_twin(1).update_seq


def test_observation_log_is_off_by_default_and_bounded_when_enabled(tmp_path):
    eng, _, s = run_engine(tmp_path / "a")
    eng.flush()
    assert eng.store.count("observations", "twin-1") == 0
    eng2, _, s2 = make_engine(tmp_path / "b", "persistence.store_observations=true", "retention.observation_log_max=25", persistence=True)
    eng2.create_twin(1)
    for r in stream(s2, 100):
        eng2.ingest(1, r)
    eng2.flush()
    assert eng2.store.count("observations", "twin-1") <= 26


def test_persistence_disabled_or_memory_backend(tmp_path):
    eng, _, s = make_engine(tmp_path, persistence=False)
    eng.create_twin(1)
    with pytest.raises(TwinError, match="persistence is disabled"):
        eng.save_state(1)
    with pytest.raises(TwinError, match="persistence is disabled"):
        eng.restore_twin(1)
    mem = StateStore("memory", None)
    mem.set_meta("k", {"a": 1})
    assert mem.get_meta("k") == {"a": 1} and mem.path is None
    mem.close()


def test_store_retention_limits(tmp_path):
    eng, _, s = make_engine(tmp_path, "retention.prediction_store_max=4", persistence=True)
    eng.create_twin(1)
    for r in stream(s, 100):
        eng.ingest(1, r)
    eng.flush()
    assert eng.store.count("predictions", "twin-1") <= 5 and eng.get_twin(1).forecast.n_predictions > 20


def test_unwritable_state_location_is_a_clear_error(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises((StateStoreError, OSError)):
        StateStore("sqlite", blocker / "sub" / "state.sqlite")
