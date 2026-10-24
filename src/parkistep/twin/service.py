"""Application-facing service: builds an engine from configuration and exposes a stable, versioned view of a twin.

The view keeps five things in separate sections so a later dashboard cannot blur them:
  model_score        - what the Phase 2 model estimated (and whether it is current)
  warning_policy     - what the configured warning policy made of the scores
  observed           - measurements actually received
  data_quality       - whether the stream satisfied the model's input requirements
  clinical_context   - recorded clinical history (and whether it influenced the model)
Annotated ground truth is never part of this view.
"""
from __future__ import annotations

import logging
from pathlib import Path

from .config import Phase3Settings
from .engine import TwinEngine, observation_clock, wall_clock
from .model import ModelHandle, load_model
from .state import DISCLAIMER, ForecastStatus, jsonable
from .storage import StateStore
from .streams import Phase1ClinicalSource

log = logging.getLogger(__name__)
VIEW_SCHEMA = "parkistep.twin.view/1"
LIMITATIONS = [
    "Scores come from a research model trained on a small cohort of structured laboratory recordings; Phase 2 reported low and variable event sensitivity and wide uncertainty across participants.",
    "Scores are uncalibrated and are not probabilities of a freezing episode; warnings are policy outputs, not clinical alerts.",
    "No clinical validation; not a medical device; not for diagnosis or treatment decisions.",
]


def build_engine(s: Phase3Settings, *, handle: ModelHandle | None = None, clock=None, clinical_source="auto",
                 store: StateStore | None = None, persistence: bool | None = None) -> TwinEngine:
    pc = s.cfg["persistence"]
    use_store = pc["enabled"] if persistence is None else persistence
    if store is None and use_store:
        store = StateStore(pc["backend"], s.state_path if pc["backend"] == "sqlite" else None, s.cfg["retention"])
    if clinical_source == "auto":
        clinical_source = Phase1ClinicalSource(s) if s.cfg["patient"]["clinical_source"] == "phase1_processed" else None
    if clock is None:
        clock = observation_clock if s.cfg["replay"]["clock"] == "observation" else wall_clock
    return TwinEngine(s, handle=handle, store=store, clinical_source=clinical_source, clock=clock)


class TwinService:
    def __init__(self, engine: TwinEngine):
        self.engine = engine

    # delegation of the engine operations a dashboard needs
    def create_twin(self, patient_id, clinical=None): return self.engine.create_twin(patient_id, clinical)
    def restore_twin(self, patient_id): return self.engine.restore_twin(patient_id)
    def ingest(self, patient_id, record): return self.engine.ingest(patient_id, record)
    def ingest_many(self, patient_id, records): return self.engine.ingest_many(patient_id, records)
    def end_session(self, patient_id): return self.engine.end_session(patient_id)

    def view(self, patient_id, recent_predictions: int = 20) -> dict:
        e = self.engine
        s = e.get_twin(patient_id)
        pid = s.patient_id
        latest = e.latest_prediction(pid)
        warn = e.current_warning(pid)
        fs = s.forecast
        cur = fs.status == ForecastStatus.CURRENT.value
        age = (s.stream.latest_observation_ts - latest.prediction_time) if (latest and s.stream.latest_observation_ts is not None
                                                                          and fs.status != ForecastStatus.HISTORICAL.value) else None
        return jsonable({
            "schema_version": VIEW_SCHEMA, "state_schema_version": s.schema_version, "disclaimer": DISCLAIMER, "limitations": LIMITATIONS,
            "twin": {"twin_id": s.twin_id, "patient_id": pid, "status": s.status, "status_reason": s.status_reason, "update_seq": s.update_seq,
                     "created_at": s.created_at, "updated_at": s.updated_at, "restored": s.restored,
                     "status_note": "software/data condition of the twin, not a medical statement"},
            "model_score": {"model": s.model, "forecast_status": fs.status, "forecast_status_reason": fs.status_reason,
                            "is_current": cur, "current_forecast": latest.to_dict() if (latest and cur) else None,
                            "latest_computed_forecast": latest.to_dict() if latest else None, "latest_forecast_age_s": age,
                            "n_forecasts": fs.n_predictions,
                            "recent": [p.to_dict() for p in e.recent_predictions(pid, recent_predictions)]},
            "warning_policy": {"state": s.warning.state, "policy": e.policy.to_dict(), "active_warning": warn.to_dict() if warn else None,
                               "history": [w.to_dict() for w in e.warning_history(pid)], "n_warnings": s.warning.n_warnings,
                               "suppressed_cooldown": s.warning.n_suppressed_cooldown},
            "observed": {"session_id": s.stream.session_id, "latest_observation_ts": s.stream.latest_observation_ts,
                         "latest_values": s.stream.latest_values, "n_accepted_session": s.stream.n_accepted_session,
                         "rejected_counts": s.stream.rejected_counts},
            "data_quality": {"quality_status": s.stream.quality_status, "detail": s.stream.quality_detail, "buffered_samples": s.stream.n_buffered,
                             "required_samples": s.stream.required_samples, "coverage_fraction": s.stream.coverage_fraction,
                             "missing_channels": s.stream.missing_channels, "last_gap_s": s.stream.last_gap_s,
                             "history": [ev.to_dict() for ev in e.data_quality_history(pid, 50)]},
            "clinical_context": {"status": s.clinical.status, "missing_fields": s.clinical.missing_fields, "validation": s.clinical.validation,
                                 "provenance": s.clinical.provenance, "influences_forecast": s.clinical.influences_forecast,
                                 "values": s.clinical.values},
            "movement_baseline": {"status": s.baseline.status, "n_updates": s.baseline.n_updates, "latest": s.baseline.latest,
                                  "note": s.baseline.note},
        })
