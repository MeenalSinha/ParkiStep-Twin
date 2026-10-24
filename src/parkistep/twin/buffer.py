"""Sensor ingestion: observation contract, optional reorder stage, bounded rolling buffer, window assessment.

Quality decisions reuse the Phase 2 functions (`causal_fill`, `position_quality`) so the engine and the trained model
can never disagree about what a usable window is.
"""
from __future__ import annotations

import bisect
import math
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import numpy as np

from ..forecast.features import FeatureSpec, causal_fill, position_quality
from .model import WindowRequirements

# ------------------------------------------------------------------ rejection reasons (stable, documented codes)
R_PATIENT_MISMATCH = "PATIENT_MISMATCH"
R_MISSING_FIELD = "MISSING_FIELD"
R_INVALID_TIMESTAMP = "INVALID_TIMESTAMP"
R_INVALID_VALUE = "INVALID_VALUE"
R_MISSING_CHANNEL = "MISSING_CHANNEL"
R_DUPLICATE = "DUPLICATE_TIMESTAMP"
R_CONFLICTING_DUPLICATE = "CONFLICTING_DUPLICATE"
R_OUT_OF_ORDER = "OUT_OF_ORDER"
R_TOO_LATE = "TOO_LATE_TO_REORDER"
R_SESSION_OPEN = "SESSION_CHANGE_REQUIRES_END"


@dataclass(frozen=True)
class Observation:
    patient_id: str
    session_id: str
    timestamp: float                       # observation clock, seconds
    values: Mapping[str, float]            # canonical channel -> value (NaN = missing)
    arrival_seq: int = 0                   # order of arrival at the engine (kept even if later reordered)
    received_utc: str = ""                 # processing time of arrival
    reordered: bool = False                # released out of arrival order by the reorder stage
    missing_channels: tuple = ()
    nonfinite_channels: tuple = ()


@dataclass
class MapResult:
    observation: Observation | None
    reject_reason: str | None = None
    detail: dict = field(default_factory=dict)


_UNIT = {"seconds": 1.0, "milliseconds": 1e-3, "microseconds": 1e-6}


class ObservationMapper:
    """Turns an external record (dict/row) into an Observation according to configuration."""

    def __init__(self, obs_cfg: dict, ingestion_cfg: dict, required_channels: tuple[str, ...]):
        self.cfg, self.ing, self.required = obs_cfg, ingestion_cfg, tuple(required_channels)
        ch = obs_cfg["channels"]
        self.src_to_canon = ({c: c for c in self.required} if ch["mode"] == "identity"
                             else dict(ch["mapping"]))
        self.canon_to_src = {v: k for k, v in self.src_to_canon.items()}
        tz = obs_cfg["timestamp"].get("timezone", "UTC")
        self.tz = timezone.utc if tz in ("UTC", "utc", None) else ZoneInfo(tz)

    def parse_timestamp(self, v: Any) -> float | None:
        t = self.cfg["timestamp"]
        try:
            if t["kind"] == "numeric":
                if isinstance(v, bool) or v is None:
                    return None
                x = float(v) * _UNIT[t["unit"]]
            else:
                if isinstance(v, str):
                    v = datetime.fromisoformat(v.replace("Z", "+00:00"))
                if hasattr(v, "to_pydatetime"):
                    v = v.to_pydatetime()
                if not isinstance(v, datetime):
                    return None
                if v.tzinfo is None:
                    v = v.replace(tzinfo=self.tz)
                x = v.astimezone(timezone.utc).timestamp()
        except (TypeError, ValueError, OverflowError):
            return None
        return x if math.isfinite(x) else None

    def map(self, record: Mapping[str, Any], patient_id: str | None = None, arrival_seq: int = 0, received_utc: str = "") -> MapResult:
        c = self.cfg
        for f in (c["patient_field"], c["timestamp"]["field"], *c["session_fields"]):
            if f not in record or record[f] is None:
                return MapResult(None, R_MISSING_FIELD, {"field": f})
        pid = str(record[c["patient_field"]])
        if pid.endswith(".0") and pid[:-2].isdigit():                  # 5.0 (float-typed id column) -> "5"
            pid = pid[:-2]
        ts = self.parse_timestamp(record[c["timestamp"]["field"]])
        if ts is None:
            return MapResult(None, R_INVALID_TIMESTAMP, {"value": repr(record[c["timestamp"]["field"]])[:40]})
        sid = "|".join(str(record[f]) for f in c["session_fields"])
        vals: dict[str, float] = {}
        missing, nonfinite = [], []
        for canon in self.required:
            src = self.canon_to_src.get(canon)
            if src is None or src not in record:
                if self.ing["missing_channel_policy"] == "reject":
                    return MapResult(None, R_MISSING_CHANNEL, {"channel": canon})
                vals[canon] = math.nan; missing.append(canon); continue
            v = record[src]
            try:
                x = math.nan if v is None else float(v)
            except (TypeError, ValueError):
                x = math.inf                                              # unparseable -> treated as non-finite
            if not math.isfinite(x):
                if v is not None and not (isinstance(v, float) and math.isnan(v)) and self.ing["nonfinite_policy"] == "reject":
                    return MapResult(None, R_INVALID_VALUE, {"channel": canon})
                if v is not None and not (isinstance(v, float) and math.isnan(v)):
                    nonfinite.append(canon)
                x = math.nan
            vals[canon] = x
        return MapResult(Observation(pid, sid, ts, vals, arrival_seq, received_utc, False, tuple(missing), tuple(nonfinite)))


class ReorderStage:
    """Bounded hold-back that releases observations in timestamp order once they are `tolerance_s` behind the newest
    timestamp seen. The arrival sequence of every observation is preserved (Observation.arrival_seq / .reordered)."""

    def __init__(self, tolerance_s: float, max_held: int):
        self.tol, self.max_held = float(tolerance_s), int(max_held)
        self._held: list[tuple[float, int, Observation]] = []
        self.max_ts: float | None = None

    def __len__(self):
        return len(self._held)

    def offer(self, obs: Observation, released_floor: float | None) -> tuple[str | None, list[Observation]]:
        if released_floor is not None and obs.timestamp < released_floor:
            return R_TOO_LATE, []
        if any(h[0] == obs.timestamp for h in self._held):
            return R_DUPLICATE, []
        if self.max_ts is not None and obs.timestamp < self.max_ts:
            obs = replace(obs, reordered=True)
        self.max_ts = obs.timestamp if self.max_ts is None else max(self.max_ts, obs.timestamp)
        bisect.insort(self._held, (obs.timestamp, obs.arrival_seq, obs), key=lambda x: (x[0], x[1]))
        out = []
        while self._held and (self.max_ts - self._held[0][0] >= self.tol or len(self._held) > self.max_held):
            out.append(self._held.pop(0)[2])
        return None, out

    def flush(self) -> list[Observation]:
        out = [h[2] for h in self._held]
        self._held.clear()
        self.max_ts = None
        return out


class RollingBuffer:
    """Bounded time-aware buffer of the most recent samples of ONE recording of ONE patient."""

    def __init__(self, raw_channels: tuple[str, ...], dt_s: float, max_duration_s: float, max_observations: int):
        self.channels = tuple(raw_channels)
        self.max_duration_s = float(max_duration_s)
        cap = int(math.ceil(max_duration_s / dt_s)) + 1
        self.maxlen = max(2, min(int(max_observations), cap))
        self._ts: deque[float] = deque(maxlen=self.maxlen)
        self._rows: deque[np.ndarray] = deque(maxlen=self.maxlen)
        self.origin_ts: float | None = None            # first timestamp since the last clear (for warm-up accounting)

    def __len__(self):
        return len(self._ts)

    def clear(self):
        self._ts.clear(); self._rows.clear(); self.origin_ts = None

    @property
    def last_ts(self) -> float | None:
        return self._ts[-1] if self._ts else None

    @property
    def last_row(self) -> np.ndarray | None:
        return self._rows[-1] if self._rows else None

    def append(self, obs: Observation):
        row = np.fromiter((obs.values[c] for c in self.channels), dtype=np.float64, count=len(self.channels))
        if self.origin_ts is None:
            self.origin_ts = obs.timestamp
        self._ts.append(obs.timestamp); self._rows.append(row)
        while len(self._ts) > 1 and self._ts[-1] - self._ts[0] > self.max_duration_s + 1e-9:
            self._ts.popleft(); self._rows.popleft()

    def tail(self, n: int) -> tuple[np.ndarray, np.ndarray]:
        k = min(n, len(self._ts))
        ts = np.fromiter(list(self._ts)[-k:], dtype=np.float64, count=k) if k else np.empty(0)
        rows = np.vstack(list(self._rows)[-k:]) if k else np.empty((0, len(self.channels)))
        return ts, rows

    def recent(self, n: int) -> list[dict]:
        ts, rows = self.tail(n)
        return [{"timestamp": float(t), **{c: (None if math.isnan(v) else float(v)) for c, v in zip(self.channels, r)}}
                for t, r in zip(ts, rows)]


@dataclass
class WindowAssessment:
    eligible: bool
    reason: str | None                    # None | insufficient_samples | warming_up | gap_in_window | irregular_timestamps | insufficient_sensor_quality
    window_complete: bool                 # enough contiguous samples (quality aside)
    quality_ok: bool
    n_available: int
    n_required: int
    coverage_fraction: float
    window_start_ts: float | None = None
    window_end_ts: float | None = None
    position_valid_fraction: dict = field(default_factory=dict)
    failing_positions: list = field(default_factory=list)
    max_step_s: float | None = None
    detail: str = ""

    def to_dict(self) -> dict:
        return {"eligible": self.eligible, "reason": self.reason, "window_complete": self.window_complete,
                "quality_ok": self.quality_ok, "n_available": self.n_available, "n_required": self.n_required,
                "coverage_fraction": round(self.coverage_fraction, 4), "window_start_ts": self.window_start_ts,
                "window_end_ts": self.window_end_ts, "position_valid_fraction": self.position_valid_fraction,
                "failing_positions": list(self.failing_positions), "max_step_s": self.max_step_s, "detail": self.detail}


def assess_window(buf: RollingBuffer, req: WindowRequirements, spec: FeatureSpec, max_gap_s: float,
                  warmup_extra_s: float = 0.0, with_window: bool = False):
    """Decide whether the trailing window satisfies the model's documented requirements.

    Returns the assessment (and, if `with_window`, the (timestamps, raw matrix) of exactly the trailing window).
    Never looks beyond the newest sample in the buffer; never fabricates values.
    """
    n = req.n_samples
    ts, raw = buf.tail(n)
    avail = len(ts)
    cov = min(1.0, avail / n) if n else 0.0

    def out(**kw):
        a = WindowAssessment(n_available=avail, n_required=n, coverage_fraction=kw.pop("coverage", cov),
                             window_start_ts=float(ts[0]) if avail else None, window_end_ts=float(ts[-1]) if avail else None, **kw)
        return (a, (ts, raw)) if with_window else a

    if avail < n:
        return out(eligible=False, reason="insufficient_samples", window_complete=False, quality_ok=False,
                   detail=f"{avail} of {n} samples buffered")
    steps = np.diff(ts)
    max_step = float(steps.max()) if len(steps) else 0.0
    if (steps <= 0).any():                                                    # cannot happen via the engine; defensive
        return out(eligible=False, reason="irregular_timestamps", window_complete=False, quality_ok=False, max_step_s=max_step,
                   detail="timestamps are not strictly increasing")
    if max_step > max_gap_s + 1e-12:
        gaps = np.where(steps > max_gap_s + 1e-12)[0]
        contiguous = avail - int(gaps[-1]) - 1                                    # samples after the most recent gap
        return out(eligible=False, reason="gap_in_window", window_complete=False, quality_ok=False, max_step_s=max_step,
                   coverage=min(1.0, contiguous / n), detail=f"largest step {max_step:.4f} s exceeds max_gap_s={max_gap_s:.4f}")
    span, exp = float(ts[-1] - ts[0]), (n - 1) * req.dt_s
    if abs(span - exp) > req.timestamp_tolerance_fraction * exp:                  # same rule as Phase 2 windows/inference
        return out(eligible=False, reason="irregular_timestamps", window_complete=False, quality_ok=False, max_step_s=max_step,
                   detail=f"window spans {span:.4f} s, expected {exp:.4f} s for the model's sampling interval")
    observed = (ts[-1] - buf.origin_ts) if buf.origin_ts is not None else 0.0
    if observed + 1e-9 < req.input_window_s + warmup_extra_s:
        return out(eligible=False, reason="warming_up", window_complete=False, quality_ok=False, max_step_s=max_step,
                   detail=f"{observed:.2f} s observed, {req.input_window_s + warmup_extra_s:.2f} s required before the first forecast")
    filled = causal_fill(raw, int(req.max_ffill_samples))
    q = position_quality(raw, filled, np.array([n - 1]), n, spec, float(req.min_valid_fraction))
    bad = [p for p in req.positions if not bool(q[p][0])]
    frac = {}
    for p in req.positions:
        ci = [i for i, c in enumerate(spec.raw_channels) if spec.channel_meta[c]["position"] == p]
        frac[p] = round(float((~np.isnan(raw[:, ci])).mean(axis=0).min()), 4)
    if bad:
        return out(eligible=False, reason="insufficient_sensor_quality", window_complete=True, quality_ok=False, max_step_s=max_step,
                   position_valid_fraction=frac, failing_positions=bad,
                   detail=f"sensor position(s) {bad} below the required valid-data fraction {req.min_valid_fraction}")
    return out(eligible=True, reason=None, window_complete=True, quality_ok=True, max_step_s=max_step, position_valid_fraction=frac)
