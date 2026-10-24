"""Sequential replay of a recording through the engine, and retrospective comparison with annotations.

The engine receives only stream records (identifiers, timestamp, sensor channels). Annotated events are consulted
strictly AFTER the stream has been exhausted, only to describe how the warnings related to the annotated onsets.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from ..forecast.alerts import match_alerts
from ..forecast.prepare import dataset_columns
from .config import Phase3Settings
from .engine import TwinEngine
from .errors import TwinError
from .state import DISCLAIMER, jsonable
from .streams import GroundTruth, SensorStream

log = logging.getLogger(__name__)

IN_SAMPLE_NOTE = ("The saved Phase 2 model was fitted on ALL participants, including the replayed one. A replay of dataset "
                  "recordings is therefore an in-sample software demonstration, not evidence of predictive performance.")


class ReplayRunner:
    def __init__(self, settings: Phase3Settings, engine: TwinEngine, sleep=time.sleep):
        self.s, self.engine, self.sleep = settings, engine, sleep

    def run(self, patient_id: str, stream: SensorStream, truth: GroundTruth | None = None) -> dict:
        eng, cfg = self.engine, self.s.cfg["replay"]
        speed = float(cfg["speed"])
        allowed = set(stream.describe().get("fields", []))
        preds, status_obs, updates = [], Counter(), 0
        statuses_seen, rej, unavailable = [], Counter(), Counter()
        activations, leak_violations, n_off, n_ignored, n_held = [], 0, 0, 0, 0
        prev_ts = None
        t0 = time.perf_counter()
        for rec in stream.records():
            if allowed and not set(rec) <= allowed:
                raise TwinError(f"stream record carries fields outside the declared set: {sorted(set(rec) - allowed)}")
            if speed > 0 and prev_ts is not None:
                ts_now = rec[self.s.cfg["observation"]["timestamp"]["field"]]
                self.sleep(max(0.0, (ts_now - prev_ts) / speed))
            prev_ts = rec[self.s.cfg["observation"]["timestamp"]["field"]]
            n_off += 1
            r = eng.ingest(patient_id, rec)
            if r.status == "rejected":
                rej[r.reason] += 1
            elif r.status == "ignored":
                n_ignored += 1
            elif r.status == "held":
                n_held += 1
            for u in r.updates:
                updates += 1
                status_obs[u.twin_status] += 1
                if u.transitions:
                    statuses_seen += [(u.timestamp, t["to"]) for t in u.transitions if t["kind"] == "STATUS_CHANGE"]
                if u.forecast_unavailable_reason:
                    unavailable[u.forecast_unavailable_reason] += 1
                if u.prediction is not None:
                    if u.prediction.window_end_time > u.timestamp + 1e-9:        # would mean a future sample entered the window
                        leak_violations += 1
                    preds.append(u.prediction)
                for e in u.warning_events:
                    if e.kind == "ACTIVATED":
                        activations.append(e.warning_id)
        eng.end_session(patient_id, "replay_finished")
        eng.flush()
        wall = time.perf_counter() - t0
        state = eng.get_twin(patient_id)
        warns = eng.warning_history(patient_id)
        h = eng.handle
        summary = {
            "schema": "parkistep.twin.replay_summary/1", "disclaimer": DISCLAIMER, "in_sample_note": IN_SAMPLE_NOTE,
            "patient_id": state.patient_id, "twin_id": state.twin_id, "stream": stream.describe(),
            "config_hash": self.s.config_hash, "model": h.identity() if h.available else {"status": "unavailable", "problems": h.problems},
            "threshold": h.threshold.to_dict() if h.available else None, "warning_policy": eng.policy.to_dict(),
            "model_training_scope": (h.manifest or {}).get("training", {}).get("fitted_on") if h.available else None,
            "counts": {"observations_offered": n_off, "observations_processed": updates, "rejected": dict(rej), "ignored_duplicates": n_ignored,
                       "held": n_held, "predictions": len(preds), "forecasts_unavailable": dict(unavailable),
                       "warnings_activated": len(activations), "warnings_by_status": dict(Counter(w.status for w in warns)),
                       "suppressed_cooldown": state.warning.n_suppressed_cooldown, "suppressed_status": state.warning.n_suppressed_status},
            "observations_per_status": dict(status_obs), "status_transitions": [{"ts": t, "to": s} for t, s in statuses_seen],
            "future_data_check": {"predictions_checked": len(preds), "violations": leak_violations,
                                  "rule": "window_end_time must equal the timestamp of the observation that triggered the forecast"},
            "timing": {"wall_seconds": round(wall, 2), "mean_inference_ms": round(float(np.mean([p.inference_ms for p in preds])), 2) if preds else None,
                       "max_inference_ms": round(float(max(p.inference_ms for p in preds)), 2) if preds else None, "speed": speed},
            "final_state": state.to_dict(),
        }
        summary["determinism_digest"] = self.digest(preds, warns)
        if truth is not None and cfg.get("evaluate_against_annotations") and h.available:
            summary["retrospective_comparison"] = self.compare(preds, warns, truth)
        return summary

    @staticmethod
    def digest(preds, warns) -> str:
        """Hash of the deterministic content (no wall-clock fields): identical data + configuration => identical digest."""
        core = {"p": [(p.prediction_id, p.session_id, round(p.prediction_time, 9), round(p.window_start_time, 9), round(p.window_end_time, 9),
                       round(p.probability, 12), p.threshold, p.above_threshold, p.data_quality) for p in preds],
                "w": [(w.warning_id, round(w.activated_at, 9), w.status, None if w.resolved_at is None else round(w.resolved_at, 9),
                       w.resolution_reason) for w in warns]}
        return hashlib.sha256(json.dumps(core, sort_keys=True).encode()).hexdigest()

    def compare(self, preds, warns, truth: GroundTruth) -> dict:
        """Retrospective only. Mirrors Phase 2 scoring rules: warnings whose input window overlaps an annotated freezing
        episode, or whose horizon runs past the recording end, are excluded (not valid, not false)."""
        h, req = self.engine.handle, self.engine.handle.requirements
        H, L = h.horizon_s, req.input_window_s
        pol = h.alert_policy or {}
        min_lead, extra = float(pol.get("min_lead_s", 0.0)), pol.get("extra_alerts_for_matched_event", "ignore")
        ev = truth.events
        rcol = dataset_columns(self.s.phase2)["recording"]
        out = {"labels_used_only_after_replay": True, "horizon_s": H, "recordings": [], "events": []}
        tot = Counter()
        mon_s = 0.0
        cadence = self.engine.cadence
        for rid in truth.recordings:
            e = ev[ev[rcol] == rid] if len(ev) else ev
            on, ends, ids = (e["onset_t"].to_numpy(), e["end_t"].to_numpy(), e["event_id"].to_numpy()) if len(e) else (np.array([]), np.array([]), np.array([]))
            lo, hi = truth.bounds[str(rid)]
            rp = [p for p in preds if p.session_id.split("|")[-1] == str(rid) or p.session_id == str(rid)]
            def overlaps(t):
                return bool(((on <= t) & (ends >= t - L)).any()) if len(on) else False
            elig = [p for p in rp if not overlaps(p.prediction_time) and p.prediction_time + H <= hi + 1e-9]
            mon_s += len(elig) * cadence
            cls = {"valid": [], "false": 0, "excl_freeze": 0, "excl_censored": 0}
            cand = []
            for w in [w for w in warns if w.session_id == rid or w.session_id.split("|")[-1] == str(rid)]:
                t = w.activated_at
                if overlaps(t):
                    cls["excl_freeze"] += 1
                elif t + H > hi + 1e-9:
                    cls["excl_censored"] += 1
                else:
                    cand.append(t)
            m = match_alerts(cand, on, ids, set(ids), H, min_lead, extra)
            cls["false"] = m["n_false"]
            for k in ("n_true", "n_redundant"):
                tot[k] += m[k]
            tot["false"] += cls["false"]; tot["excl_freeze"] += cls["excl_freeze"]; tot["excl_censored"] += cls["excl_censored"]
            for i, eid in enumerate(ids):
                covered = any(on[i] - H <= p.prediction_time < on[i] for p in elig)
                det = m["detected"].get(eid)
                out["events"].append({"event_id": eid, "recording": rid, "onset_t": float(on[i]), "forecast_window_before_onset": covered,
                                      "detected": det is not None, "warning_lead_s": det})
                tot["events"] += 1; tot["covered"] += int(covered); tot["detected"] += int(det is not None)
                tot["detected_covered"] += int(det is not None and covered)
            out["recordings"].append({"recording": rid, "first_t": lo, "last_t": hi, "n_events": int(len(ids)), "n_forecasts": len(rp),
                                      "n_forecasts_scored": len(elig), "warnings_false": cls["false"],
                                      "warnings_excluded_overlap_freezing": cls["excl_freeze"], "warnings_excluded_censored_future": cls["excl_censored"]})
        leads = [e["warning_lead_s"] for e in out["events"] if e["detected"]]
        hrs = mon_s / 3600.0
        out["totals"] = {"annotated_events": tot["events"], "events_with_forecast_coverage": tot["covered"], "events_detected": tot["detected"],
                         "event_sensitivity_all_events": (tot["detected"] / tot["events"]) if tot["events"] else None,
                         "event_sensitivity_covered_events": (tot["detected_covered"] / tot["covered"]) if tot["covered"] else None,
                         "valid_early_warnings": tot["n_true"], "false_warnings": tot["false"], "redundant_warnings": tot["n_redundant"],
                         "warnings_excluded_overlap_freezing": tot["excl_freeze"], "warnings_excluded_censored_future": tot["excl_censored"],
                         "scored_monitored_hours": hrs, "false_warnings_per_hour": (tot["false"] / hrs) if hrs > 0 else None,
                         "median_lead_s": float(np.median(leads)) if leads else None}
        out["note"] = ("Retrospective, single-participant/recording, in-sample for the saved model; not a performance estimate. "
                       "The participant-grouped Phase 2 evaluation reports are the reference for expected behaviour.")
        return jsonable(out)


def write_replay_outputs(s: Phase3Settings, engine: TwinEngine, summary: dict, tag: str) -> dict:
    d = s.output_dir
    d.mkdir(parents=True, exist_ok=True)
    pid = summary["patient_id"]
    files = {"summary": d / f"replay_{tag}_summary.json", "predictions": d / f"replay_{tag}_predictions.csv",
             "warnings": d / f"replay_{tag}_warnings.json", "events": d / f"replay_{tag}_events.json",
             "state": d / f"replay_{tag}_final_state.json"}
    if not s.cfg["outputs"]["overwrite"]:
        ex = [str(p) for p in files.values() if p.exists()]
        if ex:
            raise TwinError(f"outputs exist and outputs.overwrite is false: {ex}")
    slim = {k: v for k, v in summary.items() if k != "final_state"}
    files["summary"].write_text(json.dumps(jsonable(slim), indent=2))
    pd.DataFrame([p.to_dict() | {"warnings": "; ".join(p.warnings), "quality_detail": json.dumps(p.quality_detail)}
                  for p in engine.recent_predictions(pid, n=10 ** 9)]).to_csv(files["predictions"], index=False)
    files["warnings"].write_text(json.dumps(jsonable([w.to_dict() for w in engine.warning_history(pid)]), indent=2))
    files["events"].write_text(json.dumps(jsonable([e.to_dict() for e in engine.event_history(pid)]), indent=2))
    files["state"].write_text(json.dumps(jsonable(summary["final_state"]), indent=2))
    return {k: str(v) for k, v in files.items()}
