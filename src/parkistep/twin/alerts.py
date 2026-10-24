"""Runtime warning management.

A warning means: a model score met the configured alert conditions. It does not establish that a freezing episode is
occurring or will occur, and it is not a validated clinical alert.

Semantics (kept identical to the retrospective Phase 2 `raise_alerts`, which the tests verify on random sequences;
merge/cooldown boundaries use the same plain float comparisons as Phase 2, only run continuity has a 1e-9 tolerance):
  * a RUN is a sequence of consecutive above-threshold forecasts; forecasts further apart than 1.5 x cadence break it;
  * a run of length >= persistence_anchors is QUALIFYING; its activation time is the forecast that completes the
    persistence requirement;
  * qualifying runs whose start is within merge_gap_s of the previous qualifying run's end are ONE warning (merged);
  * an activation closer than cooldown_s to the previous activation is suppressed (and is not retried);
  * a warning RESOLVES at the first forecast evaluated after merge_gap_s without qualifying forecasts, provided at least
    clear.consecutive_below consecutive below-threshold forecasts have been seen.
The threshold is the one stored with the verified model artifact; it is never overridden here.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .model import ModelHandle, ThresholdInfo
from .state import PredictionRecord, WarningState

EPS = 1e-9
WARNING_NOTE = ("A warning means the model score met the configured alert conditions. It does not establish that a "
                "freezing episode is occurring or will occur.")

ACTIVE, RESOLVED, EXPIRED = "ACTIVE", "RESOLVED", "EXPIRED"


@dataclass(frozen=True)
class WarningPolicy:
    persistence_anchors: int
    merge_gap_s: float
    cooldown_s: float
    consecutive_below: int
    allow_repeat_while_active: bool
    expire_after_invalid_s: float | None
    max_active_s: float | None
    continuity_s: float                    # forecasts further apart than this are not "consecutive"
    source: str

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @staticmethod
    def from_settings(wcfg: dict, handle: ModelHandle | None, cadence_s: float) -> "WarningPolicy":
        src = wcfg["policy_source"]
        base = {"persistence_anchors": wcfg["persistence_anchors"], "merge_gap_s": wcfg["merge_gap_s"], "cooldown_s": wcfg["cooldown_s"]}
        used = src
        if src == "model_manifest" and handle is not None and handle.alert_policy:
            for k in base:
                if k in handle.alert_policy:
                    base[k] = handle.alert_policy[k]
        elif src == "model_manifest":
            used = "config (no model manifest available)"
        return WarningPolicy(int(base["persistence_anchors"]), float(base["merge_gap_s"]), float(base["cooldown_s"]),
                             int(wcfg["clear"]["consecutive_below"]), bool(wcfg["allow_repeat_while_active"]),
                             wcfg.get("expire_after_invalid_s"), wcfg.get("max_active_s"), 1.5 * float(cadence_s), used)


@dataclass
class WarningRecord:
    warning_id: str
    twin_id: str
    patient_id: str
    session_id: str
    status: str
    activated_at: float
    activation_prediction_id: str
    model_id: str
    horizon_s: float
    threshold: float
    threshold_source: str
    reason: str
    policy: dict
    last_qualifying_at: float
    last_qualifying_prediction_id: str
    n_qualifying_forecasts: int = 1
    max_score: float = 0.0
    resolved_at: float | None = None
    resolution_reason: str | None = None
    acknowledged_at: float | None = None
    acknowledged_by: str | None = None
    lifecycle: list = field(default_factory=list)          # append-only transitions, never deleted
    note: str = WARNING_NOTE

    def to_dict(self) -> dict:
        return {k: (list(v) if isinstance(v, list) else v) for k, v in self.__dict__.items()}


@dataclass
class WarningEvent:
    kind: str       # PENDING_STARTED | PENDING_CLEARED | ACTIVATED | EXTENDED | SUPPRESSED_COOLDOWN | SUPPRESSED_STATUS |
                    # RESOLVED | EXPIRED | SUPERSEDED | ACKNOWLEDGED | THRESHOLD_UNAVAILABLE
    ts: float | None
    warning_id: str | None = None
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "ts": self.ts, "warning_id": self.warning_id, "detail": dict(self.detail)}


class WarningManager:
    def __init__(self, twin_id: str, patient_id: str, policy: WarningPolicy, handle: ModelHandle | None,
                 history_max: int | None = None, start_count: int = 0):
        self.twin_id, self.patient_id, self.policy = twin_id, patient_id, policy
        self.model_id = handle.model_id if handle else None
        self.horizon_s = handle.horizon_s if handle else None
        self.threshold: ThresholdInfo = handle.threshold if handle else ThresholdInfo(None, "unavailable")
        self.history_max = history_max
        self.records: list[WarningRecord] = []
        self.active: WarningRecord | None = None
        self.state = WarningState(n_warnings=start_count)
        self._n = start_count
        self._reset_run()
        self.prev_t: float | None = None
        self.last_activation_t: float | None = None
        self.below_count = 0
        self.series_broken = False
        self.invalid_since: float | None = None
        self.sup_last_q: float | None = None            # end of the most recent SUPPRESSED qualifying group
        self._threshold_noted = False

    # ------------------------------------------------------------------ helpers
    def _reset_run(self):
        self.run_len, self.run_start_t, self.run_suppressed = 0, None, False

    def _sync(self):
        s = self.state
        s.active_warning_id = self.active.warning_id if self.active else None
        k = self.policy.persistence_anchors
        s.state = "active" if self.active else ("pending" if 0 < self.run_len < k else "none")
        s.pending_count = self.run_len if (not self.active and self.run_len < k) else 0
        s.pending_since = self.run_start_t if s.state == "pending" else None
        s.n_warnings = self._n

    def _close(self, rec: WarningRecord, status: str, ts: float, reason: str) -> WarningEvent:
        rec.status, rec.resolved_at, rec.resolution_reason = status, ts, reason
        rec.lifecycle.append({"ts": ts, "to": status, "reason": reason})
        if self.active is rec:
            self.active = None
        return WarningEvent(status if status in (RESOLVED, EXPIRED) else "SUPERSEDED", ts, rec.warning_id, {"reason": reason})

    def _trim(self):
        if self.history_max and len(self.records) > self.history_max:
            drop = len(self.records) - self.history_max
            self.records = [r for i, r in enumerate(self.records) if i >= drop or r is self.active]

    # ------------------------------------------------------------------ inputs
    def on_prediction(self, pred: PredictionRecord, suppress_reason: str | None = None) -> list[WarningEvent]:
        t, ev, k, pol = pred.prediction_time, [], self.policy.persistence_anchors, self.policy
        ev += self._time_checks(t)
        self.invalid_since = None
        thr = self.threshold.value
        if thr is None:                                                    # threshold-based warnings unavailable
            if not self._threshold_noted:
                self._threshold_noted = True
                ev.append(WarningEvent("THRESHOLD_UNAVAILABLE", t, None, {"note": self.threshold.note}))
            self.prev_t = t
            self._sync()
            return ev
        above = pred.probability >= thr
        if self.prev_t is not None and t - self.prev_t > pol.continuity_s + EPS:
            self.series_broken = True                      # forecasts are not consecutive: the series was interrupted
            if self.run_len:
                if self.run_len < k:
                    ev.append(WarningEvent("PENDING_CLEARED", t, None, {"reason": "discontinuity between forecasts"}))
                self._reset_run()
        self.prev_t = t
        if not above:
            self.below_count += 1
        if self.active is not None:                      # resolution is evaluated BEFORE the current forecast opens a new run
            a = self.active
            if (t - a.last_qualifying_at > pol.merge_gap_s) and (self.below_count >= pol.consecutive_below or self.series_broken):
                why = ("forecast series interrupted and no qualifying forecast within the merge gap" if self.series_broken
                       and self.below_count < pol.consecutive_below else "no qualifying forecast within the merge gap; score below threshold")
                ev.append(self._close(a, RESOLVED, t, why))
        self.series_broken = False
        if above:
            self.below_count = 0
            if self.run_len == 0:
                self.run_start_t = t
                if k > 1:
                    ev.append(WarningEvent("PENDING_STARTED", t, None, {"prediction_id": pred.prediction_id}))
            self.run_len += 1
            if self.run_len == k:
                ev += self._qualify(pred, suppress_reason)
            elif self.run_len > k and self.active is not None and not self.run_suppressed:
                self._extend(self.active, pred)
            elif self.run_len > k and self.run_suppressed:
                self.sup_last_q = t                      # a suppressed group keeps growing (Phase 2 merges before filtering)
        else:
            if 0 < self.run_len < k:
                ev.append(WarningEvent("PENDING_CLEARED", t, None, {"reason": "score fell below threshold"}))
            self._reset_run()
        self._sync()
        self._trim()
        return ev

    def _qualify(self, pred: PredictionRecord, suppress_reason: str | None) -> list[WarningEvent]:
        t, pol, out = pred.prediction_time, self.policy, []
        a = self.active
        if a is not None:
            gap = self.run_start_t - a.last_qualifying_at
            if gap <= pol.merge_gap_s or not pol.allow_repeat_while_active:
                self._extend(a, pred, merged=True)
                out.append(WarningEvent("EXTENDED", t, a.warning_id, {"prediction_id": pred.prediction_id, "merged_run_start": self.run_start_t}))
                return out
            out.append(self._close(a, RESOLVED, t, "superseded by a new qualifying run"))
            out[-1].kind = "SUPERSEDED"
        merged_into_suppressed = (self.sup_last_q is not None and self.run_start_t - self.sup_last_q <= pol.merge_gap_s)
        if merged_into_suppressed:                       # same group as an earlier suppressed run: also suppressed
            self.run_suppressed = True
            self.sup_last_q = t
            out.append(WarningEvent("SUPPRESSED_COOLDOWN" if suppress_reason is None else "SUPPRESSED_STATUS", t, None,
                                    {"prediction_id": pred.prediction_id, "merged_into_suppressed_group": True}))
            return out
        if suppress_reason:
            self.run_suppressed = True
            self.sup_last_q = t
            self.state.n_suppressed_status += 1
            out.append(WarningEvent("SUPPRESSED_STATUS", t, None, {"twin_status": suppress_reason, "prediction_id": pred.prediction_id}))
            return out
        if self.last_activation_t is not None and t < self.last_activation_t + pol.cooldown_s:
            self.run_suppressed = True
            self.sup_last_q = t
            self.state.n_suppressed_cooldown += 1
            out.append(WarningEvent("SUPPRESSED_COOLDOWN", t, None, {"prediction_id": pred.prediction_id,
                                                                      "last_activation": self.last_activation_t, "cooldown_s": pol.cooldown_s}))
            return out
        self._n += 1
        rec = WarningRecord(f"{self.twin_id}-w{self._n:04d}", self.twin_id, self.patient_id, pred.session_id, ACTIVE, t,
                            pred.prediction_id, pred.model_id, pred.horizon_s, float(self.threshold.value), self.threshold.source,
                            f"{pol.persistence_anchors} consecutive forecast(s) at or above the operating threshold {self.threshold.value:.4f}",
                            pol.to_dict(), t, pred.prediction_id, self.run_len, pred.probability)
        rec.lifecycle.append({"ts": t, "to": ACTIVE, "reason": rec.reason, "prediction_id": pred.prediction_id})
        self.records.append(rec)
        self.active = rec
        self.last_activation_t = t
        self.sup_last_q = None
        self.state.last_warning_at = t
        out.append(WarningEvent("ACTIVATED", t, rec.warning_id, {"prediction_id": pred.prediction_id, "score": pred.probability,
                                                                  "threshold": rec.threshold, "threshold_source": rec.threshold_source}))
        return out

    def _extend(self, rec: WarningRecord, pred: PredictionRecord, merged: bool = False):
        rec.last_qualifying_at, rec.last_qualifying_prediction_id = pred.prediction_time, pred.prediction_id
        rec.n_qualifying_forecasts += 1
        rec.max_score = max(rec.max_score, pred.probability)

    def on_invalid(self, ts: float, reason: str) -> list[WarningEvent]:
        """A forecast was due but could not be produced. Never creates a warning; expires a stale active one."""
        ev = []
        if self.run_len and self.run_len < self.policy.persistence_anchors:
            ev.append(WarningEvent("PENDING_CLEARED", ts, None, {"reason": f"no valid forecast: {reason}"}))
        self._reset_run()
        self.prev_t = None
        self.series_broken = True
        if self.invalid_since is None:
            self.invalid_since = ts
        ev += self._time_checks(ts)
        self._sync()
        return ev

    def on_time(self, ts: float) -> list[WarningEvent]:
        ev = self._time_checks(ts)
        self._sync()
        return ev

    def _time_checks(self, ts: float) -> list[WarningEvent]:
        a, pol, ev = self.active, self.policy, []
        if a is None:
            return ev
        if pol.max_active_s is not None and ts - a.activated_at >= pol.max_active_s - EPS:
            ev.append(self._close(a, EXPIRED, ts, f"maximum active time {pol.max_active_s} s reached"))
        elif pol.expire_after_invalid_s is not None and self.invalid_since is not None and ts - self.invalid_since >= pol.expire_after_invalid_s - EPS:
            ev.append(self._close(a, EXPIRED, ts, f"no valid forecast for {pol.expire_after_invalid_s} s"))
        return ev

    def on_session_boundary(self, ts: float | None, reason: str) -> list[WarningEvent]:
        """A different recording starts (or the session ended): nothing carries across, including cooldown."""
        ev = []
        if self.active is not None:
            ev.append(self._close(self.active, EXPIRED, ts if ts is not None else self.active.last_qualifying_at, reason))
        if self.run_len and self.run_len < self.policy.persistence_anchors:
            ev.append(WarningEvent("PENDING_CLEARED", ts, None, {"reason": reason}))
        self._reset_run()
        self.prev_t = self.last_activation_t = self.invalid_since = self.sup_last_q = None
        self.below_count = 0
        self.series_broken = False
        self._sync()
        return ev

    def acknowledge(self, warning_id: str, ts: float | None, by: str | None = None) -> WarningEvent:
        rec = next((r for r in self.records if r.warning_id == warning_id), None)
        if rec is None:
            raise KeyError(f"unknown warning {warning_id!r} for twin {self.twin_id}")
        if rec.acknowledged_at is None:
            rec.acknowledged_at, rec.acknowledged_by = ts, by
            rec.lifecycle.append({"ts": ts, "to": rec.status, "reason": "acknowledged", "by": by})
        return WarningEvent("ACKNOWLEDGED", ts, warning_id, {"by": by})
