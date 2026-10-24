"""Incremental replay of ONE recording through ONE Phase 3 twin engine.

Why this exists: `twin.replay.ReplayRunner.run` is a blocking loop, which would freeze a UI. A `ReplaySession` feeds the
same stream records to the same `engine.ingest` call in bounded slices (`advance`) so a dashboard can stay responsive,
pause, resume and stop - while the engine remains the only place where buffering, forecasting and warning decisions happen.

Guarantees (each covered by a test):
  * observations reach the engine strictly in stream order, each exactly once - calling `advance` again (a UI rerun, a double
    click) can never re-offer an observation, because the session owns a single forward-only iterator;
  * the stream carries no annotation columns; ground truth is held separately and released only by `retrospective()` after the
    session has ended;
  * the finished summary is built with the Phase 3 `ReplayRunner` helpers (`digest`, `compare`), so a full replay yields the same
    determinism digest as `scripts/replay_twin.py`;
  * a failure never fabricates output: the session switches to FAILED and keeps the error.
"""
from __future__ import annotations

import logging
import math
import sqlite3
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import pandas as pd

from ..twin.engine import TwinEngine
from ..twin.errors import StateStoreError, TwinError
from ..twin.replay import IN_SAMPLE_NOTE, ReplayRunner
from ..twin.state import DISCLAIMER, jsonable
from ..twin.streams import GroundTruth, SensorStream

log = logging.getLogger(__name__)
SUMMARY_SCHEMA = "parkistep.dashboard.replay_summary/1"


class SessionStatus(str, Enum):
    READY = "READY"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


FINISHED = (SessionStatus.COMPLETED, SessionStatus.STOPPED, SessionStatus.FAILED)
_END = object()


@dataclass
class StepReport:
    """What one `advance` call did (counts are for this call only)."""
    offered: int = 0
    accepted: int = 0
    rejected: int = 0
    predictions: int = 0
    warnings_activated: int = 0
    sim_seconds: float = 0.0
    status: str = SessionStatus.READY.value
    note: str | None = None


@dataclass
class ReplayProgress:
    status: str
    recording_id: str
    patient_id: str
    offered: int
    total_records: int | None
    fraction: float | None
    position_s: float | None            # observation-clock seconds since the first offered observation
    last_timestamp: float | None
    observations_processed: int
    observations_rejected: int
    observations_ignored: int
    predictions: int
    warnings_activated: int
    warnings_resolved: int
    twin_status: str | None
    error: dict | None = None
    note: str | None = None


class ReplaySession:
    def __init__(self, *, session_id: str, engine: TwinEngine, patient_id: str, recording_id: str, stream: SensorStream,
                 truth: GroundTruth, max_stored_observations: int, label: dict | None = None):
        self.session_id = session_id
        self.engine, self.patient_id, self.recording_id = engine, str(patient_id), str(recording_id)
        self.label = dict(label or {})
        self._stream, self._truth = stream, truth
        s3 = engine.s
        o = s3.cfg["observation"]
        self._ts_field = o["timestamp"]["field"]
        self._allowed = set(stream.describe().get("fields", []))
        self._total = int(stream.describe().get("n_records", 0)) or None
        ch = o["channels"]
        self._canon = (lambda k: k) if ch["mode"] == "identity" else (lambda k, m=ch["mapping"]: m.get(k, k))
        self._channel_fields = [c for c in (self._allowed or set()) if c not in (o["patient_field"], self._ts_field, *o["session_fields"])]
        self._channel_fields.sort()
        self._iter = None
        self._pending = None
        self._lock = threading.RLock()
        self.status = SessionStatus.READY
        self.error: dict | None = None
        self.stopped_early = False
        self.summary: dict | None = None
        self._max_stored = int(max_stored_observations)
        self.store_truncated = False
        self._ts: list[float] = []
        self._vals: list[tuple] = []
        # counters (derived from engine results only)
        self.offered = 0
        self.processed = 0
        self.rejected: Counter = Counter()
        self.ignored = 0
        self.held = 0
        self.n_predictions = 0
        self.n_activated = 0
        self.unavailable: Counter = Counter()
        self.status_obs: Counter = Counter()
        self.transitions: list[tuple] = []
        self.leak_violations = 0
        self.first_ts: float | None = None
        self.last_ts: float | None = None
        self.created_wall = time.time()
        self._wall_used = 0.0
        self.notes: deque = deque(maxlen=20)

    # ------------------------------------------------------------------ control
    def _require(self, *states):
        if self.status not in states:
            raise TwinError(f"replay is {self.status.value}; this action needs {[s.value for s in states]}")

    def start(self) -> SessionStatus:
        """READY -> RUNNING. Repeated calls are harmless: a session that already started is left untouched."""
        with self._lock:
            if self.status == SessionStatus.READY:
                self._iter = iter(self._stream.records())
                self.status = SessionStatus.RUNNING
            return self.status

    def pause(self) -> SessionStatus:
        with self._lock:
            if self.status == SessionStatus.RUNNING:
                self.status = SessionStatus.PAUSED
            return self.status

    def resume(self) -> SessionStatus:
        with self._lock:
            if self.status == SessionStatus.PAUSED:
                self.status = SessionStatus.RUNNING
            return self.status

    def stop(self) -> SessionStatus:
        """Finish early. The engine ends its session; the summary is flagged partial. No-op once finished."""
        with self._lock:
            if self.status in (SessionStatus.RUNNING, SessionStatus.PAUSED):
                self.stopped_early = True
                self._finish(SessionStatus.STOPPED)
            return self.status

    @property
    def is_finished(self) -> bool:
        return self.status in FINISHED

    # ------------------------------------------------------------------ stepping
    def advance(self, sim_seconds: float | None = None, max_observations: int | None = None) -> StepReport:
        """Hand the next slice of the recording to the engine.

        The slice ends when `sim_seconds` of recorded (observation-clock) time have been offered, or `max_observations` were
        offered, or the recording ends - whichever comes first. With both limits None the remaining recording is replayed.
        Does nothing unless the session is RUNNING.
        """
        with self._lock:
            rep = StepReport(status=self.status.value)
            if self.status != SessionStatus.RUNNING:
                rep.note = f"replay is {self.status.value}; nothing was offered to the engine"
                return rep
            t_start = time.perf_counter()
            slice_t0 = None
            eng = self.engine
            try:
                while True:
                    if max_observations is not None and rep.offered >= max_observations:
                        break
                    if self._pending is not None:
                        rec, self._pending = self._pending, None
                    else:
                        rec = next(self._iter, _END)
                    if rec is _END:
                        self._finish(SessionStatus.COMPLETED)
                        break
                    if self._allowed and not set(rec) <= self._allowed:
                        raise TwinError(f"stream record carries fields outside the declared set: {sorted(set(rec) - self._allowed)}")
                    ts = rec.get(self._ts_field)
                    # Pacing uses only a usable clock value. A record whose timestamp is missing / non-numeric / non-finite is NOT
                    # used for pacing (it would crash the replay); it is still offered so the engine rejects and counts it.
                    ts_ok = isinstance(ts, (int, float)) and not isinstance(ts, bool) and math.isfinite(ts)
                    if ts_ok:
                        if slice_t0 is None:
                            slice_t0 = ts
                        elif sim_seconds is not None and (ts - slice_t0) >= sim_seconds:
                            self._pending = rec        # belongs to the NEXT slice; held, never dropped or re-read
                            break
                    self.offered += 1
                    rep.offered += 1
                    if self.first_ts is None and ts_ok:
                        self.first_ts = ts
                    r = eng.ingest(self.patient_id, rec)
                    if r.status == "rejected":
                        self.rejected[r.reason] += 1
                        rep.rejected += 1
                    elif r.status == "ignored":
                        self.ignored += 1
                    elif r.status == "held":
                        self.held += 1
                    elif r.accepted:
                        rep.accepted += 1
                        self._store(rec, ts)
                    for u in r.updates:
                        self.processed += 1
                        self.status_obs[u.twin_status] += 1
                        for t in u.transitions:
                            if t["kind"] == "STATUS_CHANGE":
                                self.transitions.append((u.timestamp, t["to"]))
                        if u.forecast_unavailable_reason:
                            self.unavailable[u.forecast_unavailable_reason] += 1
                        if u.prediction is not None:
                            self.n_predictions += 1
                            rep.predictions += 1
                            if u.prediction.window_end_time > u.timestamp + 1e-9:
                                self.leak_violations += 1
                        for e in u.warning_events:
                            if e.kind == "ACTIVATED":
                                self.n_activated += 1
                                rep.warnings_activated += 1
                    if r.accepted:
                        self.last_ts = ts
                if slice_t0 is not None and self.last_ts is not None:
                    rep.sim_seconds = max(0.0, float(self.last_ts) - float(slice_t0))
            except Exception as exc:                                     # noqa: BLE001 - surfaced to the UI, never swallowed
                self._fail(exc)
            finally:
                self._wall_used += time.perf_counter() - t_start
            rep.status = self.status.value
            return rep

    # ------------------------------------------------------------------ internals
    def _store(self, rec: dict, ts: float) -> None:
        if len(self._ts) >= self._max_stored:
            if not self.store_truncated:
                self.store_truncated = True
                self.notes.append("observation store reached replay.max_stored_observations; later observations are not plotted")
            return
        self._ts.append(float(ts))
        self._vals.append(tuple(rec.get(c) for c in self._channel_fields))

    def _fail(self, exc: Exception) -> None:
        log.error("replay %s failed: %s: %s", self.session_id, type(exc).__name__, exc, exc_info=True)
        self.error = {"type": type(exc).__name__, "message": str(exc)}
        self.status = SessionStatus.FAILED
        try:
            self.engine.end_session(self.patient_id, "replay_failed")
        except Exception:                                                 # noqa: BLE001
            log.exception("could not end the twin session after a failed replay")
        if isinstance(exc, (StateStoreError, sqlite3.Error)):
            # A storage failure must not leave an open write transaction behind (it would lock the next session out).
            self.engine.abort_store(f"{type(exc).__name__}: {exc}")
        else:
            # Any other failure: release the store cleanly too, so a later session can open the same database file.
            store = self.engine.store
            if store is not None:
                try:
                    store.close()                                         # commit what the engine really recorded, then release
                    self.engine.store = None
                except Exception as e2:                                   # noqa: BLE001 - commit failed: nothing is claimed as stored
                    self.engine.abort_store(f"{type(e2).__name__}: {e2}")

    def _finish(self, final: SessionStatus) -> None:
        eng = self.engine
        try:
            eng.end_session(self.patient_id, "replay_finished" if final == SessionStatus.COMPLETED else "replay_stopped")
            eng.flush()
            if eng.store is not None:
                eng.save_state(self.patient_id)
                eng.flush()               # save_state can append a further event: commit it, or the open write transaction blocks the next session
            self.status = final
            self.summary = self._build_summary()
        except Exception as exc:                                          # noqa: BLE001
            self._fail(exc)

    def _build_summary(self) -> dict:
        eng, s3 = self.engine, self.engine.s
        pid = self.patient_id
        preds = eng.recent_predictions(pid, n=10 ** 9)
        warns = eng.warning_history(pid)
        h = eng.handle
        runner = ReplayRunner(s3, eng)
        state = eng.get_twin(pid)
        summary = {
            "schema": SUMMARY_SCHEMA, "disclaimer": DISCLAIMER, "in_sample_note": IN_SAMPLE_NOTE,
            "patient_id": state.patient_id, "twin_id": state.twin_id, "recording_id": self.recording_id,
            "partial_replay": bool(self.stopped_early), "config_hash": s3.config_hash,
            "model": h.identity() if h.available else {"status": "unavailable", "problems": h.problems},
            "threshold": h.threshold.to_dict() if h.available else None, "warning_policy": eng.policy.to_dict(),
            "model_training_scope": (h.manifest or {}).get("training", {}).get("fitted_on") if h.available else None,
            "counts": {"observations_offered": self.offered, "observations_processed": self.processed, "rejected": dict(self.rejected),
                       "ignored_duplicates": self.ignored, "held": self.held, "predictions": self.n_predictions,
                       "forecasts_unavailable": dict(self.unavailable), "warnings_activated": self.n_activated,
                       "warnings_by_status": dict(Counter(w.status for w in warns)),
                       "suppressed_cooldown": state.warning.n_suppressed_cooldown, "suppressed_status": state.warning.n_suppressed_status},
            "observations_per_status": dict(self.status_obs),
            "status_transitions": [{"ts": t, "to": s} for t, s in self.transitions],
            "future_data_check": {"predictions_checked": self.n_predictions, "violations": self.leak_violations,
                                  "rule": "window_end_time must equal the timestamp of the observation that triggered the forecast"},
            "timing": {"wall_seconds": round(self._wall_used, 2),
                       "mean_inference_ms": round(float(np.mean([p.inference_ms for p in preds])), 2) if preds else None,
                       "max_inference_ms": round(float(max(p.inference_ms for p in preds)), 2) if preds else None},
            "determinism_digest": runner.digest(preds, warns),
        }
        if self.n_predictions != len(preds):
            summary["history_note"] = (f"{self.n_predictions} forecasts were produced but {len(preds)} remain in the bounded history "
                                       "(retention.prediction_history_max); digest and retrospective view cover the retained forecasts")
        return jsonable(summary)

    # ------------------------------------------------------------------ read side
    def progress(self) -> ReplayProgress:
        total = self._total
        frac = (self.offered / total) if total else None
        pos = (self.last_ts - self.first_ts) if (self.last_ts is not None and self.first_ts is not None) else None
        ts = None
        try:
            ts = self.engine.get_twin(self.patient_id).status
        except Exception:                                                 # noqa: BLE001
            pass
        warns = self.engine.warning_history(self.patient_id)
        return ReplayProgress(
            status=self.status.value, recording_id=self.recording_id, patient_id=self.patient_id, offered=self.offered,
            total_records=total, fraction=frac, position_s=pos, last_timestamp=self.last_ts,
            observations_processed=self.processed, observations_rejected=int(sum(self.rejected.values())),
            observations_ignored=self.ignored, predictions=self.n_predictions,
            warnings_activated=len(warns), warnings_resolved=sum(1 for w in warns if w.resolved_at is not None),
            twin_status=ts, error=self.error, note=(self.notes[-1] if self.notes else None))

    def observations_frame(self, last_s: float | None = None) -> pd.DataFrame:
        """Accepted observations (canonical channel names). `last_s` keeps the trailing seconds of the observation clock."""
        if not self._ts:
            return pd.DataFrame(columns=["timestamp_s", *[self._canon(c) for c in self._channel_fields]])
        ts = np.asarray(self._ts, dtype=float)
        lo = 0
        if last_s is not None:
            lo = int(np.searchsorted(ts, ts[-1] - float(last_s), side="left"))
        arr = np.asarray(self._vals[lo:], dtype=float) if self._vals[lo:] else np.empty((0, len(self._channel_fields)))
        df = pd.DataFrame(arr, columns=[self._canon(c) for c in self._channel_fields])
        df.insert(0, "timestamp_s", ts[lo:])
        return df

    def retrospective(self) -> dict | None:
        """Ground-truth comparison. Only available AFTER the session finished; during a live replay this returns None."""
        with self._lock:
            if self.status not in (SessionStatus.COMPLETED, SessionStatus.STOPPED):
                return None
            h = self.engine.handle
            if not h.available or not self.engine.s.cfg["replay"].get("evaluate_against_annotations", True):
                return None
            truth = self._truth
            if self.stopped_early and self.last_ts is not None:
                ev = truth.events
                ev = ev[ev["onset_t"] <= self.last_ts] if len(ev) else ev
                lo = truth.bounds[self.recording_id][0] if self.recording_id in truth.bounds else self.first_ts
                truth = GroundTruth(ev, list(truth.recordings), {**truth.bounds, self.recording_id: (lo, float(self.last_ts))})
            runner = ReplayRunner(self.engine.s, self.engine)
            preds = self.engine.recent_predictions(self.patient_id, n=10 ** 9)
            warns = self.engine.warning_history(self.patient_id)
            out = runner.compare(preds, warns, truth)
            out["partial_replay"] = bool(self.stopped_early)
            out["annotated_events_table"] = jsonable(truth.events.to_dict("records")) if len(truth.events) else []
            return out

