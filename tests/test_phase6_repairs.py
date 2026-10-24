"""Phase 6 regression tests for defects found by the Phase 6 failure-path verification (real FoG-STAR data, real model).
Skips with a reason when real artifacts are absent unless PARKISTEP_REQUIRE_REAL=1."""
import math
import sqlite3

import pytest

from p3helpers import REPO, require_real
import parkistep.dashboard.service as svc_mod
from parkistep.dashboard.replay_session import SessionStatus
from parkistep.dashboard.service import SelectionError, build_service
from parkistep.twin import storage
from parkistep.twin.errors import StateStoreError

N_CAP = 1500


def _service(p4=(), p3=()):
    base = build_service(REPO / "configs/phase4.yaml", REPO, list(p4), [f"replay.max_observations={N_CAP}", *p3])
    require_real(base.model_description().get("available", False), "trained Phase 2 artifacts not available")
    ds = base.dataset_status()
    require_real(ds["available"], str(ds["problem"]))
    return base.warm_up()


def test_storage_failure_fails_replay_visibly_and_releases_the_database(tmp_path, monkeypatch):
    db = tmp_path / "state.sqlite"
    svc = _service(["replay.persist_state=true"], [f"persistence.path={db}"])
    d = svc.default_selection()
    broken = {"on": False}
    orig = storage.StateStore.append_event

    def flaky(self, *a, **k):
        if broken["on"]:
            raise StateStoreError("injected: database is locked")
        return orig(self, *a, **k)

    monkeypatch.setattr(storage.StateStore, "append_event", flaky)
    sess = svc.create_session(d["patient_id"], d["recording_id"])
    sess.start()
    svc.advance(sess, "max")
    assert sess.status == SessionStatus.RUNNING
    broken["on"] = True
    svc.advance(sess, "max")
    # the failure is surfaced, never swallowed, and the engine no longer claims to persist
    assert sess.status == SessionStatus.FAILED
    assert sess.error["type"] == "StateStoreError" and "database is locked" in sess.error["message"]
    assert sess.engine.store is None and "database is locked" in sess.engine.persistence_error
    # the store was released: no open write transaction, so another session on the same file can open and write
    broken["on"] = False
    other = next(p["patient_id"] for p in svc.list_patients() if p["patient_id"] != d["patient_id"])
    rid = svc.list_recordings(other)[0]["recording_id"]
    s2 = svc.create_session(other, rid)
    s2.start()
    rep = svc.advance(s2, "max")
    assert s2.status in (SessionStatus.RUNNING, SessionStatus.COMPLETED) and rep.offered > 0
    # uncommitted writes of the failed session were rolled back: the file never holds more predictions than were produced
    con = sqlite3.connect(db)
    n_db = con.execute("select count(*) from predictions").fetchone()[0]
    con.close()
    assert n_db <= sess.n_predictions + s2.n_predictions


def test_sqlite_error_when_opening_the_store_becomes_state_store_error(tmp_path, monkeypatch):
    svc = _service(["replay.persist_state=true"], [f"persistence.path={tmp_path / 's.sqlite'}"])
    d = svc.default_selection()

    def boom(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(svc_mod, "build_engine", boom)
    with pytest.raises(StateStoreError, match="cannot open the state store"):
        svc.create_session(d["patient_id"], d["recording_id"])
    assert svc.sessions == {}


@pytest.mark.parametrize("bad", ["not-a-timestamp", None, float("nan"), float("inf")])
def test_unusable_timestamp_is_rejected_and_counted_not_fatal(bad, monkeypatch):
    svc = _service()
    d = svc.default_selection()
    orig = svc_mod.build_replay_stream

    class Wrapped:
        def __init__(self, inner):
            self.inner = inner

        def describe(self):
            return self.inner.describe()

        def records(self):
            for i, rec in enumerate(self.inner.records()):
                if i == 400:
                    ts_key = next(k for k in rec if "time" in k.lower())
                    b = dict(rec)
                    b[ts_key] = bad
                    yield b
                yield rec

    monkeypatch.setattr(svc_mod, "build_replay_stream", lambda *a, **k: (lambda s_t: (Wrapped(s_t[0]), s_t[1]))(orig(*a, **k)))
    sess = svc.create_session(d["patient_id"], d["recording_id"])
    sess.start()
    while not sess.is_finished:
        sess.advance(sim_seconds=2.0)                            # time-sliced pacing (what the dashboard uses) reads every timestamp
    assert sess.status == SessionStatus.COMPLETED, sess.error
    assert sum(sess.rejected.values()) >= 1                      # counted, with a reason
    assert sess.n_predictions > 0                                # valid data still produced forecasts
    assert sess.error is None


def test_every_persisted_session_ends_with_no_open_write_transaction(tmp_path):
    """Found by the Phase 6 soak: after save_state a completed persisted session could keep an uncommitted write, which then made the
    next persisted replay fail with 'database is locked'. Covers every patient in the data set (discovered, not hard-coded)."""
    svc = build_service(REPO / "configs/phase4.yaml", REPO, ["replay.persist_state=true"],
                        ["replay.max_observations=600", f"persistence.path={tmp_path / 'state.sqlite'}"])
    require_real(svc.model_description().get("available", False), "trained Phase 2 artifacts not available")
    ds = svc.dataset_status()
    require_real(ds["available"], str(ds["problem"]))
    svc.warm_up()
    patients = [p["patient_id"] for p in svc.list_patients() if svc.list_recordings(p["patient_id"])]
    assert len(patients) > int(svc.s4["replay"]["keep_completed_sessions"]) + 1      # more than the retention window: handles must be reusable
    for pid in patients:
        sess = svc.create_session(pid, svc.list_recordings(pid)[0]["recording_id"])
        sess.start()
        while not sess.is_finished:
            sess.advance(sim_seconds=5.0)
        assert sess.status == SessionStatus.COMPLETED, (pid, sess.error)
        assert not sess.engine.store.db.in_transaction, f"patient {pid}: uncommitted write left open after completion"
