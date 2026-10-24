"""Application service for the dashboard.

Responsibilities: list patients/recordings/models from the real data and artifacts, create replay sessions around the Phase 3
engine, expose read-only views, route acknowledgement through the engine, and provide the saved Phase 2 evaluation.

It adds NO forecasting, buffering, thresholding or warning logic: every such decision is made by `parkistep.twin`. Recording
selection is delegated to `twin.streams.select_recordings`; the model comes from the Phase 3 model gateway (manifest + SHA-256).
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from collections import OrderedDict

import pandas as pd

from ..config import ConfigError
from ..forecast.prepare import dataset_columns
from ..twin.config import Phase3Settings, load_phase3
from ..twin.errors import ModelUnavailableError, StateStoreError, TwinError, TwinExistsError
from ..twin.model import ModelHandle, load_model
from ..twin.service import LIMITATIONS, TwinService, build_engine
from ..twin.state import jsonable
from ..twin.storage import StateStore
from ..twin.streams import Phase1ClinicalSource, build_replay_stream, load_processed_tables, select_recordings
from . import analysis
from .config import Phase4Settings
from .performance import PerformanceReports, load_performance
from .plotdata import channel_catalog, coverage_summary, select_channels, signal_traces
from .replay_session import FINISHED, ReplaySession, SessionStatus

log = logging.getLogger(__name__)
MODEL_KEY_RE = re.compile(r"^(?P<experiment>[^/]+)/(?P<model>[^/]+)/h(?P<h>[0-9.]+)$")


def model_key(experiment: str, model: str, horizon_s: float) -> str:
    h = int(horizon_s) if float(horizon_s) == int(horizon_s) else horizon_s
    return f"{experiment}/{model}/h{h}"


class SelectionError(TwinError):
    """The requested patient / recording / model / action is not valid for the current data or state."""


class DashboardService:
    def __init__(self, settings4: Phase4Settings, settings3: Phase3Settings | None = None, *, phase3_overrides: list[str] | None = None):
        self.s4 = settings4
        self._p3_overrides = list(phase3_overrides or [])
        self.s3 = settings3 or load_phase3(settings4.phase3_config_path, settings4.root, self._p3_overrides)
        self._lock = threading.RLock()
        self._sensor: pd.DataFrame | None = None
        self._data_problem: str | None = None
        self._clin_src: Phase1ClinicalSource | None = None
        self._clin_problem: str | None = None
        self._handles: dict[str, ModelHandle] = {}
        self._handle_problems: dict[str, str] = {}
        self.sessions: "OrderedDict[str, ReplaySession]" = OrderedDict()
        self.current_session_id: str | None = None
        self._seq = 0
        self._perf: PerformanceReports | None = None
        self.cols = dataset_columns(self.s3.phase2)

    def warm_up(self) -> "DashboardService":
        """Load the dataset, clinical source, default model and evaluation reports once (used by the cached base service)."""
        self._tables()
        self._clinical_source()
        self.performance()
        try:
            self.model_handle()
        except (ModelUnavailableError, SelectionError):
            pass                                                             # reported by model_description(); never hidden
        return self

    def fork(self) -> "DashboardService":
        """A service for ONE browser session: shares the read-only dataset, model handles and reports, but owns its replay
        sessions and engines, so one user's twin is never visible to or modified by another."""
        f = DashboardService(self.s4, self.s3, phase3_overrides=self._p3_overrides)
        f._sensor, f._data_problem = self._sensor, self._data_problem
        f._clin_src, f._clin_problem = self._clin_src, self._clin_problem
        f._handles, f._handle_problems = self._handles, self._handle_problems      # shared dicts (read-mostly, lock-free reads)
        f._perf = self._perf
        return f

    # ------------------------------------------------------------------ dataset
    def _tables(self) -> pd.DataFrame | None:
        with self._lock:
            if self._sensor is None and self._data_problem is None:
                try:
                    self._sensor, _ = load_processed_tables(self.s3)
                except TwinError as e:
                    self._data_problem = str(e)
                except Exception as e:                                      # noqa: BLE001 - e.g. a parquet engine is not installed
                    log.error("dataset load failed", exc_info=True)
                    self._data_problem = f"{type(e).__name__}: {e}"
            return self._sensor

    def dataset_status(self) -> dict:
        sensor = self._tables()
        if sensor is None:
            return {"available": False, "problem": self._data_problem,
                    "next_step": "Run `python scripts/prepare_data.py` (Phase 1) and check configs/datasets.yaml; for parquet files install pyarrow."}
        return {"available": True, "problem": None, "dataset": self.s3.profile.get("name"), "version": self.s3.profile.get("version"),
                "n_rows": int(len(sensor)), "n_patients": int(sensor[self.cols["participant"]].nunique()),
                "n_recordings": int(sensor[self.cols["recording"]].nunique())}

    def _clinical_source(self) -> Phase1ClinicalSource | None:
        with self._lock:
            if self._clin_src is None and self._clin_problem is None:
                if self.s3.cfg["patient"]["clinical_source"] != "phase1_processed":
                    self._clin_problem = "clinical source is disabled in the Phase 3 configuration (patient.clinical_source)"
                else:
                    try:
                        self._clin_src = Phase1ClinicalSource(self.s3)
                    except TwinError as e:
                        self._clin_problem = str(e)
            return self._clin_src

    def list_patients(self) -> list[dict]:
        sensor = self._tables()
        if sensor is None:
            return []
        c = self.cols
        g = sensor.groupby(c["participant"], sort=False)
        src = self._clinical_source()
        rows = []
        for pid, df in g:
            has_clin = bool(src is not None and src.get(str(pid)) is not None)
            rows.append({"patient_id": str(pid), "n_recordings": int(df[c["recording"]].nunique()), "has_clinical_record": has_clin})
        rows.sort(key=lambda r: (0, int(r["patient_id"])) if r["patient_id"].lstrip("-").isdigit() else (1, r["patient_id"]))
        return rows

    def list_recordings(self, patient_id) -> list[dict]:
        sensor = self._tables()
        if sensor is None:
            return []
        c = self.cols
        df = sensor[sensor[c["participant"]].astype(str) == str(patient_id)]
        labels = (self.s3.profile["sensor"].get("task_labels") or {})
        out = []
        for rid, g in df.groupby(c["recording"], sort=False):
            tid = g[c["task"]].iloc[0] if c["task"] in g.columns else None
            t = g[c["time"]]
            out.append({"recording_id": str(rid), "task_id": None if tid is None else str(tid),
                        "task_label": labels.get(int(tid)) if tid is not None and str(tid).lstrip("-").isdigit() else None,
                        "n_samples": int(len(g)), "duration_s": float(t.iloc[-1] - t.iloc[0])})
        return out

    def _select(self, *, patient_id=None, recording_id=None, policy: str | None = None) -> list[str]:
        """All recording selection is delegated to Phase 3 `select_recordings` (validates existence and ownership)."""
        sensor = self._tables()
        if sensor is None:
            raise SelectionError(self._data_problem or "dataset unavailable")
        s = load_phase3(self.s3.config_path, self.s3.root, self._p3_overrides)
        r = s.cfg["replay"]
        r["scope"] = "recording"
        r["participant_id"] = None if patient_id is None else str(patient_id)
        r["recording_id"] = None if recording_id is None else str(recording_id)
        if policy:
            r["selection_policy"] = policy
        try:
            return [str(x) for x in select_recordings(sensor, s)]
        except TwinError as e:
            raise SelectionError(str(e)) from e

    def default_selection(self) -> dict:
        """Default patient and recording per phase4.selection (no identifier is written in code or configuration)."""
        pol = self.s4["selection"]
        patients = self.list_patients()
        if not patients:
            return {"patient_id": None, "recording_id": None}
        pid = rid = None
        if pol["default_patient_policy"] == "first":
            pid = patients[0]["patient_id"]
        elif pol["default_patient_policy"] == "phase3_selection":
            rid0 = self._select()[0]
            pid = str(self._sensor.loc[self._sensor[self.cols["recording"]].astype(str) == rid0, self.cols["participant"]].iloc[0])
            if pol["default_recording_policy"] == "phase3_selection":
                rid = rid0
        if pid is not None:
            rp = pol["default_recording_policy"]
            if rid is None and rp != "none":
                rid = self.default_recording(pid, rp)
        return {"patient_id": pid, "recording_id": rid}

    def default_recording(self, patient_id, policy: str = "phase3_selection") -> str | None:
        mapped = {"first": "first", "longest": "longest", "phase3_selection": None}[policy]
        try:
            return self._select(patient_id=patient_id, policy=mapped)[0]
        except SelectionError:
            return None

    # ------------------------------------------------------------------ models
    def list_models(self) -> list[dict]:
        """Artifacts that exist on disk (manifest read only; loading and checksum verification happen on selection)."""
        d = self.s3.model_dir
        mc = self.s3.cfg["model"]
        default = model_key(mc["experiment"], mc["model"], float(mc["horizon_s"]))
        out = []
        if d.is_dir():
            for mp in sorted(d.glob("*.manifest.json")):
                try:
                    m = json.loads(mp.read_text())
                    k = model_key(m["experiment"], m["model"], float(m["horizon_s"]))
                    out.append({"key": k, "experiment": m["experiment"], "model": m["model"], "horizon_s": float(m["horizon_s"]),
                                "uses_clinical": bool(m.get("uses_clinical")), "is_default": k == default})
                except (OSError, ValueError, KeyError):
                    log.warning("unreadable model manifest %s", mp.name)
        if not self.s4["model_selection"]["allow_switching"]:
            out = [m for m in out if m["is_default"]]
        return out

    def default_model_key(self) -> str:
        mc = self.s3.cfg["model"]
        return model_key(mc["experiment"], mc["model"], float(mc["horizon_s"]))

    def _settings_for_model(self, key: str) -> Phase3Settings:
        m = MODEL_KEY_RE.match(key or "")
        if not m:
            raise SelectionError(f"invalid model key {key!r}")
        if key not in {x["key"] for x in self.list_models()}:
            raise SelectionError(f"model {key!r} is not among the artifacts found in {self.s3.model_dir}")
        ov = [*self._p3_overrides, f"model.experiment={m['experiment']}", f"model.model={m['model']}", f"model.horizon_s={m['h']}",
              "model.on_unavailable=fail"]
        return load_phase3(self.s3.config_path, self.s3.root, ov)

    def model_handle(self, key: str | None = None) -> ModelHandle:
        """Load (once) through the Phase 3 gateway: manifest checks + SHA-256. Raises ModelUnavailableError - never degrades."""
        key = key or self.default_model_key()
        with self._lock:
            if key in self._handles:
                return self._handles[key]
            try:
                h = load_model(self._settings_for_model(key))
            except ModelUnavailableError as e:
                self._handle_problems[key] = str(e)
                raise
            if not h.available:
                self._handle_problems[key] = "; ".join(h.problems)
                raise ModelUnavailableError("; ".join(h.problems))
            self._handles[key] = h
            self._handle_problems.pop(key, None)
            return h

    def model_description(self, key: str | None = None) -> dict:
        key = key or self.default_model_key()
        try:
            h = self.model_handle(key)
        except (ModelUnavailableError, SelectionError) as e:
            return {"available": False, "key": key, "problem": str(e),
                    "next_step": "Restore the artifact from a verified source or run the documented Phase 2 training; do not substitute another model."}
        req = h.requirements
        mapping = self.s3.cfg["patient"].get("clinical_feature_mapping") or {}
        used_profile = sorted({mapping.get(f, f) for f in h.clinical_fields}) if h.uses_clinical else []
        perf = self.performance()
        return {"available": True, "key": key, "identity": h.identity(), "horizon_s": h.horizon_s, "experiment": h.experiment,
                "uses_clinical": h.uses_clinical, "clinical_model_fields": list(h.clinical_fields), "clinical_profile_fields_used": used_profile,
                "window": req.to_dict(), "threshold": h.threshold.to_dict(), "alert_policy": h.alert_policy,
                "cadence_s": h.cadence_s, "n_features": len((h.manifest or {}).get("feature_manifest", {}).get("sensor_features", [])) if h.manifest else None,
                "training_scope": ((h.manifest or {}).get("training") or {}).get("fitted_on"),
                "calibration": perf.calibration_status(h.experiment, (h.manifest or {}).get("model"), h.horizon_s) if perf.available else
                {"validated_calibrated": False, "reason": "No saved evaluation available."},
                "sensor_only_note": None if h.uses_clinical else self.s4["text"]["sensor_only_clinical_note"].strip()}

    # ------------------------------------------------------------------ clinical profile
    def clinical_profile(self, patient_id, key: str | None = None) -> dict:
        pid = str(patient_id)
        src = self._clinical_source()
        if src is None:
            return {"patient_id": pid, "available": False, "problem": self._clin_problem, "fields": []}
        try:
            rec = src.get(pid)
        except TwinError as e:
            return {"patient_id": pid, "available": False, "problem": str(e), "fields": []}
        desc = self.model_description(key)
        used = set(desc.get("clinical_profile_fields_used", [])) if desc.get("available") else set()
        spec = self.s3.profile["clinical"]["fields"]
        key_field = self.s3.profile["clinical"]["key"]
        rows = []
        for name, f in spec.items():
            if name == key_field:
                continue
            v = None if rec is None else rec.get(name)
            rng = f.get("valid_range")
            status = "not_recorded" if v is None else "recorded"
            if v is not None and rng and isinstance(v, (int, float)) and not (rng[0] <= v <= rng[1]):
                status = "outside_documented_range"
            rows.append({"field": name, "value": v, "unit": f.get("unit"), "documented_range": rng, "status": status,
                         "dtype": f.get("dtype"), "used_by_selected_model": name in used})
        return {"patient_id": pid, "available": rec is not None, "problem": None if rec is not None else "no clinical record for this patient",
                "fields": rows, "n_recorded": sum(1 for r in rows if r["status"] != "not_recorded"), "n_fields": len(rows),
                "model_uses_clinical": bool(desc.get("uses_clinical")) if desc.get("available") else None,
                "sensor_only_note": desc.get("sensor_only_note") if desc.get("available") else None,
                "provenance": src.provenance}

    # ------------------------------------------------------------------ sessions
    def _next_id(self, pid, rid) -> str:
        self._seq += 1
        return f"replay-{self._seq:03d}-p{pid}-{rid}"

    def current_session(self) -> ReplaySession | None:
        return self.sessions.get(self.current_session_id) if self.current_session_id else None

    def create_session(self, patient_id, recording_id, model_key_: str | None = None, *, replace: bool = False) -> ReplaySession:
        """Create (not start) a replay session. Idempotent: an unstarted session with the same selection is returned unchanged;
        a running or paused session is never silently replaced (`replace=True` stops it first)."""
        pid, rid = str(patient_id), str(recording_id)
        mk = model_key_ or self.default_model_key()
        with self._lock:
            cur = self.current_session()
            if cur is not None and not cur.is_finished:
                same = cur.label.get("model_key") == mk and cur.patient_id == pid and cur.recording_id == rid
                if cur.status == SessionStatus.READY and same:
                    return cur
                if not replace:
                    raise SelectionError(f"a replay is already {cur.status.value.lower()} ({cur.recording_id}); stop it or choose 'replace'")
                cur.stop()
            sensor = self._tables()
            if sensor is None:
                raise SelectionError(f"dataset unavailable: {self._data_problem}")
            recs = self._select(patient_id=pid, recording_id=rid)           # existence + ownership (Phase 3 rule)
            handle = self.model_handle(mk)                                   # raises ModelUnavailableError (no fallback)
            s_run = self._settings_for_model(mk)
            persist = bool(self.s4["replay"]["persist_state"])
            if persist and not s_run.cfg["persistence"]["enabled"]:
                raise SelectionError("replay.persist_state is true but persistence is disabled in the Phase 3 configuration")
            stream, truth = build_replay_stream(sensor, s_run, recs, handle.requirements.raw_channels)
            parts = stream.describe()["participants"]
            if parts != [pid]:
                raise SelectionError(f"recording {rid!r} belongs to participant {parts}, not {pid}")
            try:
                engine = build_engine(s_run, handle=handle, persistence=persist)
            except sqlite3.Error as e:
                raise StateStoreError(f"cannot open the state store: {type(e).__name__}: {e}") from e
            try:
                engine.create_twin(pid)
            except TwinExistsError as e:
                engine.close()
                raise SelectionError(f"{e} (persisted state is never overwritten; set replay.persist_state: false or use another store)") from e
            except sqlite3.Error as e:
                engine.abort_store(f"{type(e).__name__}: {e}")
                raise StateStoreError(f"the state store rejected the new twin: {type(e).__name__}: {e}") from e
            except Exception:
                engine.close()
                raise
            sess = ReplaySession(session_id=self._next_id(pid, rid), engine=engine, patient_id=pid, recording_id=rid, stream=stream, truth=truth,
                                 max_stored_observations=self.s4["replay"]["max_stored_observations"],
                                 label={"model_key": mk, "task": next((r["task_label"] for r in self.list_recordings(pid) if r["recording_id"] == rid), None)})
            self.sessions[sess.session_id] = sess
            self.current_session_id = sess.session_id
            self._evict()
            return sess

    def _evict(self) -> None:
        keep = int(self.s4["replay"]["keep_completed_sessions"])
        fin = [sid for sid, s in self.sessions.items() if s.is_finished and sid != self.current_session_id]
        for sid in fin[:max(0, len(fin) - keep)]:
            s = self.sessions.pop(sid)
            try:
                s.engine.close()
            except Exception:                                                # noqa: BLE001
                log.exception("closing an evicted replay engine failed")

    def tick_parameters(self, speed) -> dict:
        """Slice size for one UI update from the configured refresh interval and the chosen speed (None = as fast as possible)."""
        rp = self.s4["replay"]
        if speed in (None, "max"):
            return {"sim_seconds": None, "max_observations": int(rp["max_observations_per_tick"])}
        sp = float(speed)
        allowed = [float(x) for x in rp["speed_choices"]]
        if sp not in allowed:
            raise SelectionError(f"speed {speed!r} is not one of the configured choices {allowed}")
        return {"sim_seconds": sp * float(rp["refresh_interval_s"]), "max_observations": int(rp["max_observations_per_tick"])}

    def advance(self, session: ReplaySession, speed):
        return session.advance(**self.tick_parameters(speed))

    def speed_options(self) -> list:
        rp = self.s4["replay"]
        return [*rp["speed_choices"], *(["max"] if rp.get("allow_max_speed") else [])]

    # ------------------------------------------------------------------ views (read-only)
    def view(self, session: ReplaySession, recent: int | None = None) -> dict:
        n = int(recent if recent is not None else self.s4["tables"]["recent_forecasts"])
        v = TwinService(session.engine).view(session.patient_id, n)
        v["data_mode"] = ("replay" if session.status in (SessionStatus.RUNNING, SessionStatus.PAUSED) else
                          "historical" if session.is_finished else "idle")
        v["session"] = {"session_id": session.session_id, "status": session.status.value, "recording_id": session.recording_id,
                        "model_key": session.label.get("model_key"), "task": session.label.get("task"),
                        "observations_processed": session.processed}
        return v

    def forecast_frame(self, session: ReplaySession, n: int | None = None) -> pd.DataFrame:
        n = int(n if n is not None else self.s4["plots"]["forecast_history_points"])
        recs = session.engine.recent_predictions(session.patient_id, n)
        cols = ["prediction_id", "prediction_time", "window_start_time", "window_end_time", "probability", "threshold", "above_threshold",
                "data_quality", "horizon_s", "model_id", "clinical_used"]
        return pd.DataFrame([{c: getattr(p, c) for c in cols} for p in recs], columns=cols)

    def warnings(self, session: ReplaySession) -> list[dict]:
        return jsonable([w.to_dict() for w in session.engine.warning_history(session.patient_id)])

    def prediction_by_id(self, session: ReplaySession, prediction_id: str) -> dict | None:
        for p in session.engine.recent_predictions(session.patient_id, 10 ** 9):
            if p.prediction_id == prediction_id:
                return jsonable(p.to_dict())
        return None

    def acknowledge_warning(self, session: ReplaySession, warning_id: str, by: str | None = None) -> dict:
        if not self.s4["features"]["allow_acknowledge"]:
            raise SelectionError("acknowledgement is disabled (phase4.features.allow_acknowledge)")
        return jsonable(session.engine.acknowledge_warning(session.patient_id, warning_id, by).to_dict())

    def observation_traces(self, session: ReplaySession, *, last_s: float | None = None, signals=None, positions=None) -> dict:
        pl = self.s4["plots"]
        df = session.observations_frame(last_s=last_s)
        cat = channel_catalog(self.s3.profile)
        chans = select_channels(cat, signals or pl["default_signals"], positions, present=list(df.columns))
        return {"traces": signal_traces(df, chans, max_points=pl["max_points_per_trace"], gap_factor=pl["gap_factor"]),
                "coverage": coverage_summary(df, [c["canonical"] for c in chans], gap_factor=pl["gap_factor"]),
                "n_points_window": int(len(df)), "truncated_store": session.store_truncated}

    def channel_info(self) -> list[dict]:
        return channel_catalog(self.s3.profile)

    def retrospective(self, session: ReplaySession) -> dict | None:
        """None while the replay is live (ground truth is never released early)."""
        if not self.s4["features"]["show_retrospective"]:
            return None
        ret = session.retrospective()
        if ret is None:
            return None
        h = session.engine.handle
        warns = self.warnings(session)
        ret["warning_timing"] = analysis.classify_warnings(warns, ret.get("annotated_events_table", []), h.horizon_s, h.requirements.input_window_s)
        return ret

    # ------------------------------------------------------------------ stored (historical) twins
    def stored_twins(self) -> list[dict]:
        pc = self.s3.cfg["persistence"]
        if not pc["enabled"] or pc["backend"] != "sqlite" or not self.s3.state_path.exists():
            return []
        st = StateStore(pc["backend"], self.s3.state_path, self.s3.cfg["retention"])
        try:
            return st.list_twins()
        finally:
            st.close()

    def open_stored_twin(self, patient_id) -> dict:
        """Restore a persisted twin read-only. It is HISTORICAL: nothing is current, warm-up restarts, no live data."""
        key = self.default_model_key()
        handle = self.model_handle(key)
        eng = build_engine(self._settings_for_model(key), handle=handle, persistence=True)
        try:
            try:
                eng.restore_twin(patient_id)
            except StateStoreError as e:
                raise SelectionError(str(e)) from e
            v = TwinService(eng).view(patient_id, int(self.s4["tables"]["recent_forecasts"]))
            v["data_mode"] = "historical"
            return v
        finally:
            eng.close()

    # ------------------------------------------------------------------ performance
    def performance(self) -> PerformanceReports:
        with self._lock:
            if self._perf is None:
                self._perf = load_performance(self.s4, self.s3.phase2)
            return self._perf

    def limitations(self) -> list[str]:
        return list(LIMITATIONS)

    def close(self) -> None:
        for s in list(self.sessions.values()):
            try:
                if not s.is_finished:
                    s.stop()
                s.engine.close()
            except Exception:                                                # noqa: BLE001
                log.exception("closing replay session failed")
        self.sessions.clear()
        self.current_session_id = None


def build_service(config_path="configs/phase4.yaml", project_root=None, overrides: list[str] | None = None,
                  phase3_overrides: list[str] | None = None) -> DashboardService:
    from .config import load_phase4
    s4 = load_phase4(config_path, project_root, overrides)
    return DashboardService(s4, phase3_overrides=phase3_overrides)


__all__ = ["DashboardService", "SelectionError", "build_service", "model_key", "ConfigError", "FINISHED"]
