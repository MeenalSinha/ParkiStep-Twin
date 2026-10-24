"""Sensor-stream abstraction, recording replay, ground-truth separation and clinical lookup.

The replay stream emits ONLY: patient identifier, timestamp, session/recording identifier and the sensor channels.
Annotation columns (fog, fog_severity, activity, task) are never part of a stream record; ground truth lives in a
separate `GroundTruth` object that is consulted only after a replay, never by the engine.
"""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Iterable, Iterator

import numpy as np
import pandas as pd

from ..forecast.events import extract_events
from ..forecast.prepare import Phase2Error, dataset_columns, read_table
from .config import Phase3Settings
from .errors import TwinError

log = logging.getLogger(__name__)


class SensorStream(ABC):
    """Anything that can deliver sensor records in arrival order (a recording replay, a wearable adapter, ...)."""

    @abstractmethod
    def records(self) -> Iterator[dict]:
        ...

    @abstractmethod
    def describe(self) -> dict:
        ...


class DataFrameStream(SensorStream):
    """Replays rows of a DataFrame in row order, exposing only `allowed_columns`."""

    def __init__(self, df: pd.DataFrame, allowed_columns: Iterable[str], description: dict | None = None, max_rows: int | None = None):
        self.allowed = [c for c in allowed_columns]
        missing = [c for c in self.allowed if c not in df.columns]
        if missing:
            raise TwinError(f"stream source lacks required columns {missing[:6]}")
        self.df = df[self.allowed]
        self.max_rows = max_rows
        self._desc = dict(description or {})

    def records(self) -> Iterator[dict]:
        cols = self.allowed
        arrays = [self.df[c].to_numpy() for c in cols]
        n = len(self.df) if self.max_rows is None else min(len(self.df), int(self.max_rows))
        for i in range(n):
            yield {c: (a[i].item() if hasattr(a[i], "item") else a[i]) for c, a in zip(cols, arrays)}

    def describe(self) -> dict:
        n = len(self.df) if self.max_rows is None else min(len(self.df), int(self.max_rows))
        return {**self._desc, "n_records": n, "fields": list(self.allowed)}


class GroundTruth:
    """Annotated events of the replayed recordings. Used for retrospective comparison only."""

    def __init__(self, events: pd.DataFrame, recordings: list[str], bounds: dict | None = None):
        self.events = events
        self.recordings = recordings
        self.bounds = bounds or {}                     # recording_id -> (first_t, last_t)

    @staticmethod
    def from_sensor(sensor: pd.DataFrame, s: Phase3Settings, recordings: list[str]) -> "GroundTruth":
        cols = dataset_columns(s.phase2)
        sub = sensor[sensor[cols["recording"]].isin(recordings)]
        ec = s.phase2.cfg["events"]
        ev = extract_events(sub, recording_col=cols["recording"], participant_col=cols["participant"], session_col=cols["session"],
                            task_col=cols["task"], time_col=cols["time"], label_col=cols["label"], positive_value=cols["positive"],
                            severity_col=cols["severity"], merge_gap_s=ec["merge_gap_s"], min_duration_s=ec["min_duration_s"])
        tcol = cols["time"]
        bounds = {str(r): (float(g[tcol].iloc[0]), float(g[tcol].iloc[-1])) for r, g in sub.groupby(cols["recording"], sort=False)}
        return GroundTruth(ev, list(recordings), bounds)


def stream_columns(s: Phase3Settings, required_channels: Iterable[str]) -> list[str]:
    o = s.cfg["observation"]
    ch = o["channels"]
    src = list(required_channels) if ch["mode"] == "identity" else [k for k, v in ch["mapping"].items() if v in set(required_channels)]
    out = [o["patient_field"], o["timestamp"]["field"], *o["session_fields"], *src]
    return list(dict.fromkeys(out))


def load_processed_tables(s: Phase3Settings) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read the Phase 1 processed tables (refuses non-PASS audits and synthetic data, as Phase 2 does)."""
    from ..forecast.prepare import load_phase1_tables
    try:
        return load_phase1_tables(s.phase2)
    except Phase2Error as e:
        raise TwinError(f"real dataset unavailable: {e}") from e


def select_recordings(sensor: pd.DataFrame, s: Phase3Settings) -> list[str]:
    """Deterministic selection of the recording(s) to replay from configuration (no hard-coded identifiers)."""
    r = s.cfg["replay"]
    cols = dataset_columns(s.phase2)
    rec, pid = cols["recording"], cols["participant"]
    if r.get("recording_id") is not None:
        rid = str(r["recording_id"])
        if rid not in set(sensor[rec].astype(str)):
            raise TwinError(f"replay.recording_id {rid!r} not found in the dataset")
        if r.get("participant_id") is not None:
            owner = str(sensor.loc[sensor[rec].astype(str) == rid, pid].iloc[0])
            if owner != str(r["participant_id"]):
                raise TwinError(f"recording {rid!r} belongs to participant {owner}, not {r['participant_id']}")
        return [sensor.loc[sensor[rec].astype(str) == rid, rec].iloc[0]]
    df = sensor
    if r.get("participant_id") is not None:
        df = sensor[sensor[pid].astype(str) == str(r["participant_id"])]
        if df.empty:
            raise TwinError(f"replay.participant_id {r['participant_id']!r} not found in the dataset")
    order = list(dict.fromkeys(df[rec]))
    if r["scope"] == "participant":
        if r.get("participant_id") is None:
            pol_df = df.groupby(pid, sort=False).size()
            chosen = pol_df.idxmax()
            df = df[df[pid] == chosen]
            order = list(dict.fromkeys(df[rec]))
        return order
    lens = df.groupby(rec, sort=False).size()
    if r["selection_policy"] == "first":
        return [order[0]]
    if r["selection_policy"] == "longest":
        return [lens.idxmax()]
    ev = df.groupby(rec, sort=False)[dataset_columns(s.phase2)["label"]].apply(lambda x: int((np.diff(np.r_[0, (x.to_numpy() == 1).astype(int)]) == 1).sum()))
    return [ev.idxmax()]


def build_replay_stream(sensor: pd.DataFrame, s: Phase3Settings, recordings: list[str], required_channels: Iterable[str]) -> tuple[SensorStream, GroundTruth]:
    cols = dataset_columns(s.phase2)
    sub = sensor[sensor[cols["recording"]].isin(recordings)]
    sub = pd.concat([g for _, g in sub.groupby(cols["recording"], sort=False)]) if len(recordings) > 1 else sub
    order = {r: i for i, r in enumerate(recordings)}
    sub = sub.iloc[np.argsort(sub[cols["recording"]].map(order).to_numpy(), kind="stable")]
    stream = DataFrameStream(sub, stream_columns(s, required_channels),
                             {"kind": "recording_replay", "recordings": list(map(str, recordings)),
                              "participants": sorted(map(str, sub[cols["participant"]].unique()))},
                             s.cfg["replay"].get("max_observations"))
    return stream, GroundTruth.from_sensor(sensor, s, recordings)


class Phase1ClinicalSource:
    """Read-only lookup of a patient's clinical record from the Phase 1 processed clinical table."""

    def __init__(self, s: Phase3Settings):
        d = s.phase2.base.processed_dir
        man = d / s.phase2.base.project["outputs"]["manifest"]
        if not man.exists():
            raise TwinError(f"Phase 1 manifest missing at {man}; run scripts/prepare_data.py first")
        m = json.loads(man.read_text())
        if m.get("synthetic_test_data"):
            raise TwinError("Phase 1 outputs are marked synthetic; refusing to use them")
        if m.get("audit_status") not in ("PASS", "PASS_WITH_WARNINGS"):
            raise TwinError(f"Phase 1 audit status is {m.get('audit_status')!r}")
        self.table = read_table(d / s.phase2.base.project["outputs"]["file_stems"]["clinical"])
        self.key = s.profile["clinical"]["key"]
        self.provenance = {"dataset": s.profile.get("name"), "version": s.profile.get("version"), "audit_status": m.get("audit_status")}

    def get(self, patient_id: str) -> dict | None:
        t = self.table
        rows = t[t[self.key].astype(str) == str(patient_id)]
        if len(rows) > 1:
            raise TwinError(f"clinical table has {len(rows)} records for patient {patient_id!r}; expected one")
        if rows.empty:
            return None
        return {k: (None if (isinstance(v, float) and np.isnan(v)) else (v.item() if hasattr(v, "item") else v)) for k, v in rows.iloc[0].to_dict().items()}
