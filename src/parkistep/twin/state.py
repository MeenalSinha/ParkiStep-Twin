"""Digital-twin state schema and lifecycle.

Statuses describe the SOFTWARE/DATA condition of the twin. They are not medical statements: e.g. DEGRADED_DATA_QUALITY
means the measurements do not satisfy the model's input requirements, not that the patient's condition changed.

Four things are kept apart on purpose (see docs/phase3/PHASE3_IMPLEMENTATION.md):
  1. recorded clinical history        -> ClinicalProfile
  2. statistics derived from observed movement -> BaselineState (descriptive only)
  3. learned parameters of the Phase 2 model  -> never stored in the twin (only the model identity)
  4. predictions of the Phase 2 model          -> PredictionRecord (immutable) / ForecastState
"""
from __future__ import annotations

import math
import typing
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from enum import Enum

from .config import SCHEMA_VERSION

DISCLAIMER = "Research model score; not a diagnosis and not a validated clinical warning."
SCORE_SEMANTICS = ("uncalibrated model score in [0, 1]; it is not a calibrated probability and the Phase 2 model is not "
                   "clinically validated")


class TwinStatus(str, Enum):
    INITIALIZING = "INITIALIZING"
    WAITING_FOR_DATA = "WAITING_FOR_DATA"
    WARMING_UP = "WARMING_UP"
    READY = "READY"
    DEGRADED_DATA_QUALITY = "DEGRADED_DATA_QUALITY"
    STALE = "STALE"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    ERROR = "ERROR"


class ForecastStatus(str, Enum):
    NONE = "none"                  # no forecast has been computed in this session
    CURRENT = "current"            # the latest forecast was computed from the current stream and is fresh
    STALE = "stale"                # the latest forecast is older than the configured freshness limit
    UNAVAILABLE = "unavailable"    # a forecast was due but the input did not satisfy the model's requirements
    HISTORICAL = "historical"      # forecasts exist only from a previous run (restored state)


@dataclass(frozen=True)
class StatusFacts:
    """Everything the lifecycle decision depends on. The decision is a pure function of these facts."""
    error: bool
    model_available: bool
    stale: bool
    has_observation: bool          # any accepted observation in the current session
    window_complete: bool          # enough contiguous samples (and warm-up) for one model window
    window_quality_ok: bool        # that window satisfies the model's sensor-quality requirements
    clinical_ok: bool = True       # fusion models only: required clinical fields present when policy = require_all


def derive_status(f: StatusFacts) -> tuple[TwinStatus, str]:
    """Deterministic lifecycle rule (first match wins). Returns (status, reason)."""
    if f.error:
        return TwinStatus.ERROR, "an unexpected error occurred while processing; see the audit events"
    if not f.model_available:
        return TwinStatus.MODEL_UNAVAILABLE, "the selected forecasting model could not be loaded; observations are tracked, no forecasts are made"
    if f.stale:
        return TwinStatus.STALE, "no accepted observation within the configured freshness limit"
    if not f.has_observation:
        return TwinStatus.WAITING_FOR_DATA, "no observation received in the current session"
    if not f.window_complete:
        return TwinStatus.WARMING_UP, "the rolling window does not yet hold enough contiguous samples"
    if not f.clinical_ok:
        return TwinStatus.DEGRADED_DATA_QUALITY, "required clinical fields are missing and the configured policy forbids imputation"
    if not f.window_quality_ok:
        return TwinStatus.DEGRADED_DATA_QUALITY, "the window does not satisfy the model's sensor-quality requirements"
    return TwinStatus.READY, "the window satisfies the model's input requirements"


@dataclass(frozen=True)
class PredictionRecord:
    """Immutable record of one model inference. Never modified after creation."""
    prediction_id: str
    twin_id: str
    patient_id: str
    session_id: str
    sequence: int                          # n-th forecast of this twin (1-based)
    prediction_time: float                 # observation clock: timestamp of the newest sample used
    window_start_time: float
    window_end_time: float
    probability: float                     # model score in [0, 1] (uncalibrated)
    horizon_s: float
    model_id: str
    model_artifact_sha256: str
    uses_clinical: bool                    # does the selected model take clinical input?
    clinical_used: bool                    # did clinical input actually enter this forecast?
    threshold: float | None
    threshold_source: str
    above_threshold: bool | None
    data_quality: str
    quality_detail: dict
    warnings: tuple
    inference_ms: float
    processing_time_utc: str
    state_version: int                     # update_seq of the state that produced it
    score_semantics: str = SCORE_SEMANTICS
    disclaimer: str = DISCLAIMER

    def to_dict(self) -> dict:
        d = asdict(self)
        d["warnings"] = list(self.warnings)
        return d

    @staticmethod
    def from_dict(d: dict) -> "PredictionRecord":
        d = dict(d)
        d["warnings"] = tuple(d.get("warnings", ()))
        names = {f.name for f in fields(PredictionRecord)}
        return PredictionRecord(**{k: v for k, v in d.items() if k in names})


@dataclass
class TwinEvent:
    """Audit / data-quality / lifecycle event. Append-only."""
    seq: int
    ts: float | None                       # observation clock (None when not tied to an observation)
    kind: str
    detail: dict = field(default_factory=dict)
    processing_time_utc: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ClinicalProfile:
    status: str = "absent"                 # valid | partial | absent | invalid_record
    values: dict = field(default_factory=dict)
    missing_fields: list = field(default_factory=list)
    validation: dict = field(default_factory=dict)      # field -> ok | missing | out_of_range
    provenance: dict = field(default_factory=dict)      # source, key field/value, row hash, dataset
    influences_forecast: bool = False                   # true only if the selected model takes clinical input


@dataclass
class SensorStreamState:
    session_id: str | None = None
    latest_observation_ts: float | None = None
    latest_values: dict | None = None
    n_buffered: int = 0
    buffer_duration_s: float = 0.0
    required_samples: int = 0
    coverage_fraction: float = 0.0
    quality_status: str = "none"           # none | ok | insufficient_samples | gap | insufficient_sensor_quality
    quality_detail: dict = field(default_factory=dict)  # per sensor position valid fraction etc.
    missing_channels: list = field(default_factory=list)
    last_gap_s: float | None = None
    gap_active: bool = False
    n_accepted_session: int = 0
    n_accepted_total: int = 0
    rejected_counts: dict = field(default_factory=dict)
    last_processing_time_utc: str | None = None


@dataclass
class ForecastState:
    status: str = ForecastStatus.NONE.value
    status_reason: str = "no forecast computed yet"
    latest_prediction_id: str | None = None
    latest_prediction_time: float | None = None
    last_attempt_ts: float | None = None
    next_due_ts: float | None = None
    n_predictions: int = 0
    n_pruned: int = 0
    n_unavailable: int = 0


@dataclass
class WarningState:
    state: str = "none"                    # none | pending | active
    active_warning_id: str | None = None
    pending_count: int = 0
    pending_since: float | None = None
    last_warning_at: float | None = None
    n_warnings: int = 0
    n_suppressed_cooldown: int = 0
    n_suppressed_status: int = 0


@dataclass
class BaselineState:
    enabled: bool = False
    status: str = "disabled"               # disabled | warming_up | reporting | frozen
    n_updates: int = 0
    positions: dict = field(default_factory=dict)   # position -> {mean, var, n}
    latest: dict = field(default_factory=dict)      # position -> {value, z}
    note: str = ("descriptive statistics of observed movement; not a model input, not a personalised disease model, "
                 "not clinically validated")


@dataclass
class TwinState:
    twin_id: str
    patient_id: str
    created_at: str
    updated_at: str
    schema_version: str = SCHEMA_VERSION
    update_seq: int = 0
    status: str = TwinStatus.INITIALIZING.value
    status_reason: str = "twin created"
    status_since_ts: float | None = None
    clinical: ClinicalProfile = field(default_factory=ClinicalProfile)
    stream: SensorStreamState = field(default_factory=SensorStreamState)
    forecast: ForecastState = field(default_factory=ForecastState)
    warning: WarningState = field(default_factory=WarningState)
    baseline: BaselineState = field(default_factory=BaselineState)
    model: dict = field(default_factory=dict)
    config: dict = field(default_factory=dict)
    restored: dict | None = None
    disclaimer: str = DISCLAIMER

    def to_dict(self) -> dict:
        return jsonable(asdict(self))

    @staticmethod
    def from_dict(d: dict) -> "TwinState":
        return _build(TwinState, d)


def jsonable(x):
    """Recursively make a structure JSON-safe (NaN/inf -> None, tuples -> lists, enums -> values)."""
    if isinstance(x, dict):
        return {str(k): jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [jsonable(v) for v in x]
    if isinstance(x, Enum):
        return x.value
    if isinstance(x, float) and not math.isfinite(x):
        return None
    if hasattr(x, "item") and not isinstance(x, (str, bytes)):     # numpy scalar
        try:
            return jsonable(x.item())
        except Exception:
            return str(x)
    return x


def _build(cls, d: dict):
    hints = typing.get_type_hints(cls)
    kwargs = {}
    for f in fields(cls):
        if f.name not in d:
            continue
        v, t = d[f.name], hints.get(f.name)
        if is_dataclass(t) and isinstance(v, dict):
            v = _build(t, v)
        kwargs[f.name] = v
    return cls(**kwargs)
