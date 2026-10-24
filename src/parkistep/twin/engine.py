"""Digital-twin engine: one isolated runtime per patient; every accepted observation updates the twin.

Per accepted observation (see docs/phase3/PHASE3_IMPLEMENTATION.md): validate -> identity/session checks -> order/duplicate/gap
checks -> buffer -> assess window -> (if due and eligible) call the Phase 2 predictor on the trailing window ONLY ->
prediction history -> warning policy -> baseline -> lifecycle status -> persistence -> structured result.
The predictor is never called when the model's documented input requirements are not met, and a prediction is never
re-labelled as new: `PredictionRecord`s are immutable and `ForecastState.status` says whether the latest one is current.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import threading
import time
import traceback
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Mapping

import numpy as np
import pandas as pd

from ..forecast.inference import InferenceInputError
from .alerts import ACTIVE, EXPIRED, RESOLVED, WarningEvent, WarningManager, WarningPolicy, WarningRecord
from .baseline import MovementBaseline
from .buffer import (R_CONFLICTING_DUPLICATE, R_DUPLICATE, R_OUT_OF_ORDER, R_PATIENT_MISMATCH, R_SESSION_OPEN, Observation,
                     ObservationMapper, ReorderStage, RollingBuffer, WindowAssessment, assess_window)
from .config import SCHEMA_VERSION, Phase3Settings
from .errors import (PatientMismatchError, StateIncompatibleError, TwinError, TwinExistsError, UnknownTwinError)
from .model import ModelHandle, load_model
from .state import (ClinicalProfile, ForecastStatus, PredictionRecord, StatusFacts, TwinEvent, TwinState, TwinStatus,
                    derive_status, jsonable)
from .storage import StateStore

log = logging.getLogger(__name__)
audit = logging.getLogger("parkistep.twin.audit")
DUE_TOL = 1e-6


def wall_clock(_ts=None) -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def observation_clock(ts=None) -> str:
    """Deterministic processing-time stamp for replays: derived from the observation clock, not the wall clock."""
    return "obs-clock:" + ("none" if ts is None else f"{ts:.6f}")


@dataclass
class UpdateResult:
    """Outcome of processing ONE observation."""
    update_seq: int
    timestamp: float
    twin_status: str
    prediction: PredictionRecord | None = None          # present only if a model inference happened in THIS update
    forecast_attempted: bool = False
    forecast_status: str = ForecastStatus.NONE.value
    forecast_unavailable_reason: str | None = None
    warning_events: list = field(default_factory=list)
    assessment: dict = field(default_factory=dict)
    transitions: list = field(default_factory=list)


@dataclass
class IngestResult:
    status: str                         # accepted | held | rejected | ignored
    reason: str | None
    patient_id: str
    twin_id: str | None
    detail: dict = field(default_factory=dict)
    updates: list = field(default_factory=list)         # one UpdateResult per processed observation

    @property
    def accepted(self) -> bool:
        return self.status == "accepted"

    @property
    def prediction(self) -> PredictionRecord | None:
        ps = [u.prediction for u in self.updates if u.prediction is not None]
        return ps[-1] if ps else None

    @property
    def warning_events(self) -> list:
        return [e for u in self.updates for e in u.warning_events]


class TwinRuntime:
    """Everything private to one twin. Nothing here is shared with another patient."""

    def __init__(self, state: TwinState, buffer: RollingBuffer, warnings: WarningManager, baseline: MovementBaseline,
                 reorder: ReorderStage | None, history: dict):
        self.state, self.buffer, self.warnings, self.baseline, self.reorder = state, buffer, warnings, baseline, reorder
        self.predictions: deque[PredictionRecord] = deque(maxlen=history.get("prediction_history_max"))
        self.events: deque[TwinEvent] = deque(maxlen=history.get("event_history_max"))
        self.event_seq = 0
        self.arrival_seq = 0
        self.attempt_due_ts: float | None = None
        self.last_released_ts: float | None = None
        self.last_assessment: WindowAssessment | None = None
        self.clinical_for_model: dict = {}
        self.clinical_missing_for_model: list = []
        self.error = False
        self.stale = False


class TwinEngine:
    def __init__(self, settings: Phase3Settings, handle: ModelHandle | None = None, store: StateStore | None = None,
                 clinical_source=None, clock: Callable | None = None):
        self.s = settings
        cfg = settings.cfg
        self.handle = handle if handle is not None else load_model(settings)
        self.store = store
        self.persistence_error: str | None = None      # set when a storage failure aborted persistence for this engine
        self.clinical_source = clinical_source
        self.clock = clock or wall_clock
        self._lock = threading.RLock()
        self._twins: dict[str, TwinRuntime] = {}
        self._pat_re = __import__("re").compile(cfg["patient"]["id_pattern"])
        h = self.handle
        raw = h.requirements.raw_channels if h.available else tuple(self._fallback_channels())
        self.mapper = ObservationMapper(cfg["observation"], cfg["ingestion"], raw)
        self.dt = h.requirements.dt_s if h.available else float(cfg["model"].get("expected_dt_s") or 1.0 / float(settings.profile["sampling"]["expected_hz"]))
        self.cadence = h.cadence_s if h.available else float(cfg["cadence"].get("every_s") or 0.2)
        gap = cfg["ingestion"].get("max_gap_s")
        self.max_gap = float(gap) if gap else float(settings.profile["sampling"]["gap_threshold_factor"]) * self.dt
        self.policy = WarningPolicy.from_settings(cfg["warning"], h if h.available else None, self.cadence)
        self.history = cfg["retention"]
        self.audit_on = bool(cfg["logging"]["audit"]["enabled"])
        self.log_rejections = bool(cfg["logging"]["audit"]["log_rejections"])
        if self.store is not None:
            self.store.set_meta("config_snapshot", settings.snapshot())
            self.store.set_meta("model_identity", h.identity() if h.available else {"status": "unavailable", "problems": h.problems})

    def _fallback_channels(self):
        from ..config import channel_specs
        return [c["canonical"] for c in channel_specs(self.s.profile)]

    # ------------------------------------------------------------------ helpers
    @property
    def threshold_value(self):
        return self.handle.threshold.value if self.handle.available else None

    def _twin_id(self, patient_id: str) -> str:
        return self.s.cfg["patient"]["twin_id_template"].format(patient_id=patient_id)

    def _rt(self, patient_id) -> TwinRuntime:
        pid = self._norm_pid(patient_id)
        rt = self._twins.get(pid)
        if rt is None:
            raise UnknownTwinError(f"no twin for patient {pid!r}; create or restore it first")
        return rt

    @staticmethod
    def _norm_pid(p) -> str:
        s = str(p)
        return s[:-2] if s.endswith(".0") and s[:-2].isdigit() else s

    def _event(self, rt: TwinRuntime, ts, kind: str, detail: dict | None = None, persist: bool = True) -> TwinEvent:
        rt.event_seq += 1
        ev = TwinEvent(rt.event_seq, ts, kind, jsonable(detail or {}), self.clock(ts))
        rt.events.append(ev)
        if persist and self.store is not None:
            self.store.append_event(rt.state.twin_id, ts, kind, ev.detail, ev.processing_time_utc)
        if self.audit_on and kind in ("STATUS_CHANGE", "SESSION_START", "SESSION_END", "GAP_RESET", "RESTORED", "ERROR", "MODEL_UNAVAILABLE"):
            audit.info("patient=%s kind=%s %s", rt.state.patient_id, kind, json.dumps(ev.detail, default=str)[:300])
        return ev

    def _warn_events(self, rt: TwinRuntime, events: list[WarningEvent]):
        for e in events:
            self._event(rt, e.ts, "WARNING_" + e.kind, {"warning_id": e.warning_id, **e.detail})
            if e.warning_id and self.store is not None:
                rec = next((r for r in rt.warnings.records if r.warning_id == e.warning_id), None)
                if rec is not None:
                    self.store.upsert_warning(rt.state.twin_id, rec.to_dict())
            if self.audit_on and e.kind in ("ACTIVATED", "RESOLVED", "EXPIRED", "SUPERSEDED"):
                audit.info("patient=%s warning=%s %s", rt.state.patient_id, e.kind, e.warning_id)

    def _facts(self, rt: TwinRuntime) -> StatusFacts:
        a = rt.last_assessment
        clinical_ok = not (self.handle.available and self.handle.uses_clinical
                           and self.s.cfg["patient"]["clinical_missing_policy"] == "require_all" and rt.clinical_missing_for_model)
        return StatusFacts(rt.error, self.handle.available, rt.stale, rt.state.stream.n_accepted_session > 0,
                           bool(a and a.window_complete), bool(a and a.quality_ok), clinical_ok)

    def _refresh_status(self, rt: TwinRuntime, ts, transitions: list | None = None) -> bool:
        st, why = derive_status(self._facts(rt))
        s = rt.state
        if st.value == s.status:
            s.status_reason = why
            return False
        old = s.status
        s.status, s.status_reason, s.status_since_ts = st.value, why, ts
        tr = {"from": old, "to": st.value, "reason": why}
        self._event(rt, ts, "STATUS_CHANGE", tr)
        if transitions is not None:
            transitions.append({"kind": "STATUS_CHANGE", **tr})
        return True

    def _touch(self, rt: TwinRuntime, ts):
        rt.state.update_seq += 1
        rt.state.updated_at = self.clock(ts)

    def _sync_forecast_counters(self, rt: TwinRuntime):
        rt.state.warning = rt.warnings.state

    def _snapshot(self, rt: TwinRuntime, force=False, had_event=False):
        if self.store is None:
            return
        pol = self.s.cfg["persistence"]["snapshot_policy"]
        if force or pol == "every_update" or (pol == "on_event" and had_event):
            self.save_state(rt.state.patient_id)

    # ------------------------------------------------------------------ creation / retrieval
    def create_twin(self, patient_id, clinical: Mapping | None = None) -> TwinState:
        with self._lock:
            pid = self._norm_pid(patient_id)
            cfgp = self.s.cfg["patient"]
            if not self._pat_re.match(pid):
                raise TwinError(f"invalid patient identifier {pid!r} (must match {cfgp['id_pattern']})")
            existing = self._twins.get(pid)
            persisted = self.store is not None and self.store.twin_exists(pid)
            if persisted and existing is None and cfgp["exists_policy"] == "error" and self.s.cfg["persistence"]["overwrite"]:
                self.store.delete_twin(self._twin_id(pid))               # explicit overwrite policy: replace the persisted twin
                persisted = False
            if existing is not None or persisted:
                if cfgp["exists_policy"] == "return_existing":
                    if existing is None:
                        return self.restore_twin(pid)
                    return existing.state
                raise TwinExistsError(f"a twin for patient {pid!r} already exists (patient.exists_policy=error)")
            record = dict(clinical) if clinical is not None else None
            if record is None and cfgp["clinical_source"] != "none" and self.clinical_source is not None:
                record = self.clinical_source.get(pid)
            if record is None and cfgp["require_clinical_record"]:
                raise TwinError(f"no clinical record found for patient {pid!r} and patient.require_clinical_record is true")
            profile = self._build_clinical(pid, record)
            now = self.clock(None)
            state = TwinState(self._twin_id(pid), pid, now, now, clinical=profile,
                              model=self._model_block(), config={"config_hash": self.s.config_hash, "compat_hash": self.s.compat_hash,
                                                                  "warning_policy": self.policy.to_dict()})
            rt = self._make_runtime(state)
            self._twins[pid] = rt
            self._event(rt, None, "TWIN_CREATED", {"clinical_status": profile.status, "model_available": self.handle.available})
            if not self.handle.available:
                self._event(rt, None, "MODEL_UNAVAILABLE", {"problems": self.handle.problems})
            self._refresh_status(rt, None)
            self._touch(rt, None)
            if self.store is not None:
                self.store.save_twin(self._persistable_state(rt), self.s.compat_hash, self.handle.model_id, self.handle.artifact_sha256,
                                     replace=bool(self.s.cfg["persistence"]["overwrite"]))
            log.info("twin created: patient=%s status=%s clinical=%s", pid, state.status, profile.status)
            return state

    def _model_block(self) -> dict:
        h = self.handle
        if not h.available:
            return {"status": "unavailable", "problems": list(h.problems)}
        return {"status": "available", **h.identity(), "threshold": h.threshold.to_dict(), "window": h.requirements.to_dict(),
                "cadence_s": h.cadence_s, "clinical_influences_forecast": h.uses_clinical}

    def _make_runtime(self, state: TwinState) -> TwinRuntime:
        h, cfg = self.handle, self.s.cfg
        req = h.requirements
        channels = req.raw_channels if h.available else tuple(self._fallback_channels())
        buf = RollingBuffer(channels, self.dt, cfg["buffer"]["max_duration_s"], cfg["buffer"]["max_observations"])
        oo = cfg["ingestion"]["out_of_order"]
        reorder = ReorderStage(oo["reorder_tolerance_s"], oo["max_held"]) if oo["policy"] == "reorder_within_tolerance" else None
        wm = WarningManager(state.twin_id, state.patient_id, self.policy, h if h.available else None,
                            self.history.get("warning_history_max"), state.warning.n_warnings)
        bl = MovementBaseline(cfg["baseline"], h.spec if h.available else None, req.positions if h.available else (), state.baseline)
        old = state.warning
        wm.state.n_suppressed_cooldown, wm.state.n_suppressed_status, wm.state.last_warning_at = old.n_suppressed_cooldown, old.n_suppressed_status, old.last_warning_at
        state.warning = wm.state
        rt = TwinRuntime(state, buf, wm, bl, reorder, self.history)
        state.stream.required_samples = req.n_samples if h.available else 0
        self._apply_clinical_for_model(rt)
        return rt

    def _build_clinical(self, pid: str, record: Mapping | None) -> ClinicalProfile:
        prof, cfgp = self.s.profile, self.s.cfg["patient"]
        fields = prof["clinical"]["fields"]
        key = prof["clinical"]["key"]
        if record is None:
            return ClinicalProfile("absent", {}, [f for f in fields if f != key], {}, {"source": "none"}, self.handle.uses_clinical)
        if key in record and self._norm_pid(record[key]) != pid:
            raise PatientMismatchError(f"clinical record belongs to patient {self._norm_pid(record[key])!r}, not {pid!r}")
        if key not in record:
            raise PatientMismatchError(f"clinical record carries no {key!r}; it cannot be verified to belong to patient {pid!r}")
        vals, val, missing = {}, {}, []
        for f, spec in fields.items():
            if f == key:
                continue
            v = record.get(f)
            if v is None or (isinstance(v, float) and math.isnan(v)):
                missing.append(f); val[f] = "missing"; vals[f] = None; continue
            v = v.item() if hasattr(v, "item") else v
            vals[f] = v
            vr = spec.get("valid_range")
            val[f] = "out_of_range" if (vr and isinstance(v, (int, float)) and not (vr[0] <= v <= vr[1])) else "ok"
        status = "valid" if not missing and all(x == "ok" for x in val.values()) else "partial"
        row_hash = hashlib.sha256(json.dumps({k: vals[k] for k in sorted(vals)}, default=str).encode()).hexdigest()
        provenance = {"source": cfgp["clinical_source"] if self.clinical_source is not None else "supplied", "key_field": key,
                      "key_value": pid, "row_sha256": row_hash, "dataset": prof.get("name"), "dataset_version": prof.get("version")}
        return ClinicalProfile(status, vals, missing, val, provenance, self.handle.uses_clinical)

    def _apply_clinical_for_model(self, rt: TwinRuntime):
        rt.clinical_for_model, rt.clinical_missing_for_model = {}, []
        if not (self.handle.available and self.handle.uses_clinical):
            return
        mp = self.s.cfg["patient"].get("clinical_feature_mapping") or {}
        for f in self.handle.clinical_fields:
            v = rt.state.clinical.values.get(mp.get(f, f))
            rt.clinical_for_model[f] = v
            if v is None:
                rt.clinical_missing_for_model.append(f)

    def get_twin(self, patient_id) -> TwinState:
        with self._lock:
            return self._rt(patient_id).state

    def has_twin(self, patient_id) -> bool:
        return self._norm_pid(patient_id) in self._twins

    def patients(self) -> list[str]:
        return sorted(self._twins)

    # ------------------------------------------------------------------ ingestion
    def ingest(self, patient_id, record: Mapping) -> IngestResult:
        with self._lock:
            rt = self._rt(patient_id)
            rt.arrival_seq += 1
            mr = self.mapper.map(record, arrival_seq=rt.arrival_seq, received_utc=self.clock(None))
            if mr.observation is None:
                return self._reject(rt, mr.reject_reason, mr.detail, None)
            return self.ingest_observation(patient_id, mr.observation)

    def ingest_many(self, patient_id, records) -> list[IngestResult]:
        """Same sequential semantics as ingest() called once per record, in order."""
        return [self.ingest(patient_id, r) for r in records]

    def ingest_observation(self, patient_id, obs: Observation) -> IngestResult:
        with self._lock:
            rt = self._rt(patient_id)
            if obs.patient_id != rt.state.patient_id:
                return self._reject(rt, R_PATIENT_MISMATCH, {"observation_patient": obs.patient_id}, obs.timestamp)
            return self._submit(rt, obs)

    def _reject(self, rt: TwinRuntime, reason: str, detail: dict, ts) -> IngestResult:
        rc = rt.state.stream.rejected_counts
        rc[reason] = rc.get(reason, 0) + 1
        if self.log_rejections:
            self._event(rt, ts, "OBSERVATION_REJECTED", {"reason": reason, **detail})
        return IngestResult("rejected", reason, rt.state.patient_id, rt.state.twin_id, detail)

    def _submit(self, rt: TwinRuntime, obs: Observation) -> IngestResult:
        st = rt.state.stream
        updates: list[UpdateResult] = []
        if st.session_id is not None and obs.session_id != st.session_id:
            if self.s.cfg["ingestion"]["session_change_policy"] == "require_explicit_end":
                return self._reject(rt, R_SESSION_OPEN, {"current_session": st.session_id, "observation_session": obs.session_id}, obs.timestamp)
            if rt.reorder is not None:
                for o in rt.reorder.flush():
                    r = self._process(rt, o)
                    if isinstance(r, UpdateResult):
                        updates.append(r)
            self._close_session(rt, obs.timestamp, "session_change", next_session=obs.session_id)
        if st.session_id is None:
            self._open_session(rt, obs)
        if rt.reorder is None:
            r = self._process(rt, obs)
            if isinstance(r, IngestResult):
                r.updates = updates + r.updates
                return r
            return IngestResult("accepted", None, rt.state.patient_id, rt.state.twin_id, updates=updates + [r])
        reason, released = rt.reorder.offer(obs, rt.last_released_ts)
        if reason is not None:
            return self._reject(rt, reason, {"timestamp": obs.timestamp}, obs.timestamp)
        for o in released:
            r = self._process(rt, o)
            if isinstance(r, UpdateResult):
                updates.append(r)
            if o.reordered:
                self._event(rt, o.timestamp, "OBSERVATION_REORDERED", {"arrival_seq": o.arrival_seq})
        return IngestResult("accepted" if released else "held", None, rt.state.patient_id, rt.state.twin_id, updates=updates)

    def _open_session(self, rt: TwinRuntime, obs: Observation):
        st = rt.state.stream
        st.session_id, st.n_accepted_session = obs.session_id, 0
        rt.attempt_due_ts = None
        rt.last_released_ts = None
        rt.stale = False
        self._event(rt, obs.timestamp, "SESSION_START", {"session_id": obs.session_id})
        self._warn_events(rt, rt.warnings.on_session_boundary(obs.timestamp, "new session"))
        if self.s.cfg["baseline"]["reset_on_session_change"]:
            rt.baseline.reset()

    def _close_session(self, rt: TwinRuntime, ts, reason: str, next_session=None):
        st = rt.state.stream
        self._event(rt, ts, "SESSION_END", {"session_id": st.session_id, "reason": reason, "next_session": next_session,
                                           "n_accepted": st.n_accepted_session})
        self._warn_events(rt, rt.warnings.on_session_boundary(ts, f"session ended ({reason})"))
        rt.buffer.clear()                                   # nothing from the old recording can enter a new window
        st.session_id, st.n_accepted_session, st.n_buffered, st.buffer_duration_s = None, 0, 0, 0.0
        st.latest_values, st.coverage_fraction, st.gap_active = None, 0.0, False
        st.quality_status, st.quality_detail = "none", {}
        rt.last_assessment, rt.attempt_due_ts, rt.last_released_ts = None, None, None
        f = rt.state.forecast
        if f.status in (ForecastStatus.CURRENT.value, ForecastStatus.STALE.value, ForecastStatus.UNAVAILABLE.value):
            f.status, f.status_reason = ForecastStatus.NONE.value, f"session ended ({reason}); forecasts of the old session are historical"
        rt.state.warning = rt.warnings.state
        self._refresh_status(rt, ts)
        self._touch(rt, ts)
        self._snapshot(rt, had_event=True)

    def end_session(self, patient_id, reason: str = "explicit") -> TwinState:
        with self._lock:
            rt = self._rt(patient_id)
            if rt.state.stream.session_id is not None:
                if rt.reorder is not None:
                    for o in rt.reorder.flush():
                        self._process(rt, o)
                self._close_session(rt, rt.buffer.last_ts, reason)
            return rt.state

    # ------------------------------------------------------------------ the update step
    def _process(self, rt: TwinRuntime, obs: Observation):
        s, st, buf = rt.state, rt.state.stream, rt.buffer
        ts = obs.timestamp
        last = buf.last_ts
        transitions: list = []
        wev: list[WarningEvent] = []
        if last is not None:
            if ts == last:
                row = buf.last_row
                same = np.array_equal(row, np.array([obs.values[c] for c in buf.channels]), equal_nan=True)
                if self.s.cfg["ingestion"]["duplicate_policy"] == "ignore_identical" and same:
                    return IngestResult("ignored", R_DUPLICATE, s.patient_id, s.twin_id, {"timestamp": ts, "identical": True})
                return self._reject(rt, R_CONFLICTING_DUPLICATE if (not same and self.s.cfg["ingestion"]["duplicate_policy"] == "ignore_identical") else R_DUPLICATE,
                                    {"timestamp": ts, "identical_payload": bool(same)}, ts)
            if ts < last:
                return self._reject(rt, R_OUT_OF_ORDER, {"timestamp": ts, "latest_accepted": last}, ts)
        rt.last_released_ts = ts
        rt.error = False if self.s.cfg["lifecycle"]["error_recovery"] == "auto" else rt.error
        rt.stale = False
        try:
            if last is not None and ts - last > self.max_gap + 1e-12:
                st.last_gap_s = ts - last
                self._event(rt, ts, "GAP_DETECTED", {"gap_s": ts - last, "max_gap_s": self.max_gap, "policy": self.s.cfg["ingestion"]["gap_policy"]})
                wev += rt.warnings.on_invalid(last, "observation gap")
                if self.s.cfg["ingestion"]["gap_policy"] == "reset_buffer":
                    buf.clear(); rt.attempt_due_ts = None; rt.last_assessment = None
                    self._event(rt, ts, "GAP_RESET", {"gap_s": ts - last})
                    f = s.forecast
                    f.status, f.status_reason = ForecastStatus.UNAVAILABLE.value, f"observation gap of {ts - last:.3f} s; rolling window reset"
                    st.gap_active = False
                else:
                    st.gap_active = True
                wev += rt.warnings.on_time(ts)
            buf.append(obs)
            st.n_accepted_session += 1
            st.n_accepted_total += 1
            st.latest_observation_ts = ts
            st.latest_values = {c: (None if math.isnan(v) else v) for c, v in obs.values.items()}
            st.last_processing_time_utc = self.clock(ts)
            st.missing_channels = list(obs.missing_channels)
            if obs.nonfinite_channels:
                self._event(rt, ts, "DATA_QUALITY", {"issue": "non-finite values treated as missing", "channels": list(obs.nonfinite_channels)})
            if obs.missing_channels and st.n_accepted_session == 1:
                self._event(rt, ts, "DATA_QUALITY", {"issue": "channels absent from observations", "channels": list(obs.missing_channels)})
            if self.store is not None and self.s.cfg["persistence"]["store_observations"]:
                self.store.append_observation(s.twin_id, ts, obs.session_id, st.latest_values)
            self._touch(rt, ts)
            res = UpdateResult(s.update_seq, ts, s.status)
            res.warning_events = wev
            if self.handle.available:
                self._assess_and_forecast(rt, obs, res)
            else:
                st.n_buffered, st.buffer_duration_s = len(buf), float((ts - buf.origin_ts) if buf.origin_ts is not None else 0.0)
            self._refresh_staleness(rt, ts)
        except Exception as e:                                                       # never let one bad update take the engine down
            rt.error = True
            detail = {"type": type(e).__name__, "message": str(e)[:300]}
            if self.s.cfg["logging"]["error_reporting"]["include_traceback"]:
                detail["traceback"] = traceback.format_exc()[-1500:]
            self._event(rt, ts, "ERROR", detail)
            log.error("update failed for patient=%s: %s", s.patient_id, detail["message"])
            res = UpdateResult(s.update_seq, ts, s.status)
        self._refresh_status(rt, ts, res.transitions)
        res.twin_status = s.status
        res.forecast_status = s.forecast.status
        self._sync_forecast_counters(rt)
        self._warn_events(rt, res.warning_events)
        had = bool(res.prediction or res.warning_events or res.transitions or res.forecast_attempted)
        self._snapshot(rt, had_event=had)
        return res

    def _assess_and_forecast(self, rt: TwinRuntime, obs: Observation, res: UpdateResult):
        s, st, h, req = rt.state, rt.state.stream, self.handle, self.handle.requirements
        ts = obs.timestamp
        a, (wts, wraw) = assess_window(rt.buffer, req, h.spec, self.max_gap, float(self.s.cfg["buffer"]["warmup_extra_s"]), with_window=True)
        rt.last_assessment = a
        res.assessment = a.to_dict()
        st.n_buffered = len(rt.buffer)
        st.buffer_duration_s = float(ts - rt.buffer.origin_ts) if rt.buffer.origin_ts is not None else 0.0
        st.coverage_fraction = a.coverage_fraction
        st.quality_status = "ok" if a.eligible else (a.reason or "none")
        st.quality_detail = {"position_valid_fraction": a.position_valid_fraction, "failing_positions": a.failing_positions,
                             "detail": a.detail, "max_step_s": a.max_step_s}
        due = rt.attempt_due_ts is None and a.window_complete or (rt.attempt_due_ts is not None and ts >= rt.attempt_due_ts - DUE_TOL)
        if not due:
            return
        if rt.attempt_due_ts is None:
            rt.attempt_due_ts = ts
        rt.attempt_due_ts += self.cadence
        while rt.attempt_due_ts <= ts + DUE_TOL:
            rt.attempt_due_ts += self.cadence
        s.forecast.next_due_ts = rt.attempt_due_ts
        s.forecast.last_attempt_ts = ts
        res.forecast_attempted = True
        if not a.eligible:
            self._forecast_unavailable(rt, res, ts, a.reason or "ineligible", a.detail)
            return
        clinical_ok = not (h.uses_clinical and self.s.cfg["patient"]["clinical_missing_policy"] == "require_all" and rt.clinical_missing_for_model)
        if not clinical_ok:
            self._forecast_unavailable(rt, res, ts, "clinical_missing", f"missing model clinical fields {rt.clinical_missing_for_model}; policy require_all")
            return
        df = pd.DataFrame(wraw, columns=list(req.raw_channels))
        df.insert(0, req.timestamp_col, wts)
        t0 = time.perf_counter()
        try:
            out = h.predictor.predict(df, req.timestamp_col, clinical=(dict(rt.clinical_for_model) if h.uses_clinical else None),
                                      metadata={"twin_id": s.twin_id, "state_version": s.update_seq})
        except InferenceInputError as e:                        # the predictor's own guard disagreed with ours: treat as no forecast
            self._forecast_unavailable(rt, res, ts, "predictor_rejected_input", str(e))
            return
        ms = (time.perf_counter() - t0) * 1000.0
        if out.data_quality != "ok" or out.probability is None:
            self._forecast_unavailable(rt, res, ts, out.data_quality, "; ".join(out.warnings))
            return
        seq = s.forecast.n_predictions + 1
        thr = h.threshold.value
        pred = PredictionRecord(
            f"{s.twin_id}-p{seq:06d}", s.twin_id, s.patient_id, st.session_id, seq, float(out.prediction_time), float(out.window_start_time),
            float(wts[-1]), float(out.probability), float(out.horizon_s), h.model_id, h.artifact_sha256, h.uses_clinical,
            bool(h.uses_clinical), thr, h.threshold.source, (bool(out.probability >= thr) if thr is not None else None), out.data_quality,
            {"position_valid_fraction": a.position_valid_fraction, "features_expected": out.features_expected,
             "features_finite": out.features_finite}, tuple(out.warnings), round(ms, 3), self.clock(ts), s.update_seq)
        if len(rt.predictions) == rt.predictions.maxlen:
            s.forecast.n_pruned += 1
        rt.predictions.append(pred)
        f = s.forecast
        f.n_predictions, f.latest_prediction_id, f.latest_prediction_time = seq, pred.prediction_id, pred.prediction_time
        f.status, f.status_reason = ForecastStatus.CURRENT.value, "computed from the current rolling window"
        if self.store is not None:
            self.store.append_prediction(s.twin_id, pred.to_dict(), seq)
        res.prediction = pred
        facts_status = derive_status(self._facts(rt))[0].value
        sup = facts_status if facts_status in self.s.cfg["warning"]["suppress_when_status"] else None
        res.warning_events += rt.warnings.on_prediction(pred, sup)
        rt.state.warning = rt.warnings.state
        if rt.baseline.enabled:
            rt.baseline.observe(rt.baseline.summarize(wraw), facts_status, rt.warnings.state.state)

    def _forecast_unavailable(self, rt: TwinRuntime, res: UpdateResult, ts, reason: str, detail: str):
        f = rt.state.forecast
        f.status, f.status_reason = ForecastStatus.UNAVAILABLE.value, f"{reason}: {detail}" if detail else reason
        f.n_unavailable += 1
        res.forecast_unavailable_reason = reason
        self._event(rt, ts, "FORECAST_UNAVAILABLE", {"reason": reason, "detail": detail})
        res.warning_events += rt.warnings.on_invalid(ts, reason)
        rt.state.warning = rt.warnings.state

    def _refresh_staleness(self, rt: TwinRuntime, now_ts):
        f = rt.state.forecast
        lim = float(self.s.cfg["forecast"]["stale_after_cadences"]) * self.cadence
        if f.status == ForecastStatus.CURRENT.value and f.latest_prediction_time is not None and now_ts - f.latest_prediction_time > lim:
            f.status, f.status_reason = ForecastStatus.STALE.value, f"latest forecast is older than {lim:.2f} s"

    def tick(self, patient_id, now_ts: float) -> TwinState:
        """Advance time without data (live use): freshness of the forecast and of the stream, warning expiry."""
        with self._lock:
            rt = self._rt(patient_id)
            tr: list = []
            ev = rt.warnings.on_time(now_ts)
            self._refresh_staleness(rt, now_ts)
            lim = self.s.cfg["lifecycle"].get("stale_after_s")
            li = rt.state.stream.latest_observation_ts
            if lim and li is not None and now_ts - li > float(lim) and rt.state.stream.session_id is not None:
                rt.stale = True
                if rt.state.forecast.status == ForecastStatus.CURRENT.value:
                    rt.state.forecast.status, rt.state.forecast.status_reason = ForecastStatus.STALE.value, "no recent observations"
            changed = self._refresh_status(rt, now_ts, tr)
            self._warn_events(rt, ev)
            rt.state.warning = rt.warnings.state
            if changed or ev:
                self._touch(rt, now_ts)
                self._snapshot(rt, had_event=True)
            return rt.state

    # ------------------------------------------------------------------ warnings API
    def acknowledge_warning(self, patient_id, warning_id: str, by: str | None = None) -> WarningRecord:
        with self._lock:
            rt = self._rt(patient_id)
            ev = rt.warnings.acknowledge(warning_id, rt.state.stream.latest_observation_ts, by)
            self._warn_events(rt, [ev])
            self._touch(rt, ev.ts)
            return next(r for r in rt.warnings.records if r.warning_id == warning_id)

    # ------------------------------------------------------------------ inspection (read-only copies)
    def latest_prediction(self, patient_id) -> PredictionRecord | None:
        rt = self._rt(patient_id)
        return rt.predictions[-1] if rt.predictions else None

    def recent_predictions(self, patient_id, n: int = 20) -> list[PredictionRecord]:
        return list(self._rt(patient_id).predictions)[-n:] if n else []

    def current_warning(self, patient_id) -> WarningRecord | None:
        return self._rt(patient_id).warnings.active

    def warning_history(self, patient_id) -> list[WarningRecord]:
        return list(self._rt(patient_id).warnings.records)

    def event_history(self, patient_id, kind: str | None = None, limit: int | None = None) -> list[TwinEvent]:
        ev = [e for e in self._rt(patient_id).events if kind is None or e.kind == kind or (kind.endswith("*") and e.kind.startswith(kind[:-1]))]
        return ev[-limit:] if limit else ev

    def data_quality_history(self, patient_id, limit: int | None = None) -> list[TwinEvent]:
        kinds = ("DATA_QUALITY", "FORECAST_UNAVAILABLE", "GAP_DETECTED", "GAP_RESET", "OBSERVATION_REJECTED")
        ev = [e for e in self._rt(patient_id).events if e.kind in kinds]
        return ev[-limit:] if limit else ev

    def recent_observations(self, patient_id, n: int = 20) -> list[dict]:
        return self._rt(patient_id).buffer.recent(n)

    # ------------------------------------------------------------------ persistence
    def save_state(self, patient_id) -> None:
        if self.store is None:
            raise TwinError("persistence is disabled")
        with self._lock:
            rt = self._rt(patient_id)
            self.store.save_twin(self._persistable_state(rt), self.s.compat_hash, self.handle.model_id, self.handle.artifact_sha256)
            for r in rt.warnings.records:
                self.store.upsert_warning(rt.state.twin_id, r.to_dict())

    def _persistable_state(self, rt: TwinRuntime) -> dict:
        """The ONE place that turns a twin into a stored snapshot: clinical values are minimised unless configured otherwise."""
        d = rt.state.to_dict()
        if not self.s.cfg["persistence"]["store_clinical_values"]:
            d["clinical"] = {**d["clinical"], "values": {}, "values_stored": False}
        return d

    def flush(self):
        if self.store is not None:
            self.store.flush()

    def abort_store(self, reason: str) -> None:
        """Called after a failure while storing: roll back what was not committed, release the database and record that this
        engine no longer persists. In-memory state stays readable; nothing is reported as stored that was not."""
        if self.store is None:
            return
        log.error("persistence aborted: %s", reason)
        self.store.abort()
        self.store = None
        self.persistence_error = reason

    def close(self):
        """Persist a final snapshot of every loaded twin (unless snapshot_policy is manual) and close the store."""
        if self.store is not None:
            if self.s.cfg["persistence"]["snapshot_policy"] != "manual":
                for pid in list(self._twins):
                    self.save_state(pid)
            self.store.close()

    def restore_twin(self, patient_id) -> TwinState:
        """Rebuild a twin from persisted state. The rolling buffer is NOT restored: the twin re-enters warm-up, old forecasts
        stay history (`historical`), and an active warning is expired because no live data support it."""
        with self._lock:
            pid = self._norm_pid(patient_id)
            if self.store is None:
                raise TwinError("persistence is disabled; nothing to restore")
            if pid in self._twins:
                raise TwinExistsError(f"twin for patient {pid!r} is already loaded")
            row = self.store.load_twin(pid)
            if row is None:
                raise UnknownTwinError(f"no persisted twin for patient {pid!r}")
            if row["schema_version"] != SCHEMA_VERSION:
                raise StateIncompatibleError(f"persisted state schema {row['schema_version']!r} != supported {SCHEMA_VERSION!r}")
            if self.handle.available and (row["model_id"] != self.handle.model_id or row["model_sha256"] != self.handle.artifact_sha256):
                raise StateIncompatibleError(f"persisted state was produced by model {row['model_id']} ({str(row['model_sha256'])[:12]}), "
                                             f"but the engine runs {self.handle.model_id} ({self.handle.artifact_sha256[:12]})")
            cfg_changed = row["compat_hash"] != self.s.compat_hash
            if cfg_changed and self.s.cfg["persistence"]["on_incompatible"] != "allow_config_change":
                raise StateIncompatibleError("persisted state was produced under a different state-affecting configuration "
                                             "(set persistence.on_incompatible=allow_config_change to restore anyway; warm-up restarts)")
            try:
                state = TwinState.from_dict(row["state"])
            except Exception as e:
                raise StateIncompatibleError(f"persisted state of patient {pid!r} cannot be interpreted: {e}") from e
            prof = state.clinical
            if not prof.values and prof.provenance.get("row_sha256"):               # values were not stored: re-read and verify
                src = self.clinical_source.get(pid) if self.clinical_source is not None else None
                if src is None:
                    prof.status, prof.validation = "absent", {}
                    prof.provenance = {**prof.provenance, "restore_note": "clinical source unavailable at restore; values not re-attached"}
                else:
                    fresh = self._build_clinical(pid, src)
                    if fresh.provenance["row_sha256"] != prof.provenance["row_sha256"]:
                        raise StateIncompatibleError("clinical record changed since the state was saved (hash mismatch)")
                    state.clinical = fresh
            now = self.clock(None)
            prior_seq, prior_status = state.update_seq, state.status
            state.restored = {"restored_at": now, "prior_update_seq": prior_seq, "prior_status": prior_status,
                              "config_changed": cfg_changed,
                              "note": "rolling buffer not restored; the twin re-enters warm-up; earlier forecasts are historical"}
            state.status, state.status_reason = TwinStatus.INITIALIZING.value, "restored from persisted state"
            st = state.stream
            st.session_id, st.n_accepted_session, st.n_buffered, st.buffer_duration_s = None, 0, 0, 0.0
            st.latest_values, st.coverage_fraction, st.quality_status, st.quality_detail, st.gap_active = None, 0.0, "none", {}, False
            state.warning.state, state.warning.active_warning_id, state.warning.pending_count, state.warning.pending_since = "none", None, 0, None
            f = state.forecast
            f.status, f.status_reason = (ForecastStatus.HISTORICAL.value, "forecasts exist only from a previous run") if f.n_predictions else (ForecastStatus.NONE.value, "no forecast computed yet")
            f.next_due_ts = f.last_attempt_ts = None
            state.model, state.config = self._model_block(), {"config_hash": self.s.config_hash, "compat_hash": self.s.compat_hash,
                                                              "warning_policy": self.policy.to_dict()}
            state.baseline.enabled = bool(self.s.cfg["baseline"]["enabled"])
            rt = self._make_runtime(state)
            for d in self.store.list_predictions(state.twin_id, self.history.get("prediction_history_max")):
                rt.predictions.append(PredictionRecord.from_dict(d))
            for d in self.store.list_warnings(state.twin_id):
                rec = WarningRecord(**d)
                if rec.status == ACTIVE:
                    rec.status, rec.resolved_at, rec.resolution_reason = EXPIRED, rec.last_qualifying_at, "restored_without_live_data"
                    rec.lifecycle.append({"ts": rec.last_qualifying_at, "to": EXPIRED, "reason": "restored_without_live_data"})
                    self.store.upsert_warning(state.twin_id, rec.to_dict())
                rt.warnings.records.append(rec)
            rt.event_seq = self.store.count("events", state.twin_id)
            self._twins[pid] = rt
            self._event(rt, None, "RESTORED", state.restored)
            self._refresh_status(rt, None)
            state.update_seq = prior_seq
            self._touch(rt, None)
            self.save_state(pid)
            log.info("twin restored: patient=%s prior_seq=%d status=%s", pid, prior_seq, state.status)
            return state
