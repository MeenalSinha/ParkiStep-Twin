"""Reader for the saved Phase 2 evaluation outputs (`phase2_metrics.json`).

Nothing here computes or invents a metric: values are copied from the saved file, missing entries stay `None`, and every row
carries the denominators needed to interpret it. Participant-grouped out-of-fold results are the primary evidence; the
in-sample replay summary is a different object and is never mixed into these tables.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..twin.errors import TwinError

KEY_SEP = "|"


def _get(d, *path):
    cur = d
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur


def _ci(entry, name):
    c = _get(entry, "bootstrap", name, "ci")
    return list(c) if isinstance(c, (list, tuple)) and len(c) == 2 else None


class PerformanceReports:
    def __init__(self, metrics_path: Path, report_path: Path | None = None):
        self.metrics_path = Path(metrics_path)
        self.report_path = Path(report_path) if report_path else None
        self.problem: str | None = None
        self.data: dict | None = None
        if not self.metrics_path.is_file():
            self.problem = (f"Phase 2 metrics file not found: {self.metrics_path}. Run `python scripts/train_phase2.py` and "
                            "`python scripts/evaluate_phase2.py` (or restore the saved reports) to enable this page.")
            return
        try:
            self.data = json.loads(self.metrics_path.read_text())
            if not isinstance(self.data.get("results"), dict):
                raise ValueError("no `results` mapping")
        except (OSError, ValueError) as e:
            self.data = None
            self.problem = f"Phase 2 metrics file {self.metrics_path} could not be read: {e}"

    @property
    def available(self) -> bool:
        return self.data is not None

    @property
    def experiment_id(self) -> str | None:
        return (self.data or {}).get("experiment_id")

    @property
    def config_hash(self) -> str | None:
        return (self.data or {}).get("config_hash")

    @staticmethod
    def result_key(experiment: str, model: str, horizon_s: float) -> str:
        h = f"h{int(horizon_s)}" if float(horizon_s) == int(horizon_s) else f"h{horizon_s}"
        return KEY_SEP.join([experiment, model, h])

    def entry(self, experiment: str, model: str, horizon_s: float) -> dict | None:
        if not self.available:
            return None
        return self.data["results"].get(self.result_key(experiment, model, horizon_s))

    def rows(self) -> list[dict]:
        """One flat row per evaluated (experiment, model, horizon); numbers exactly as saved, with their denominators."""
        out = []
        for key, r in (self.data or {}).get("results", {}).items():
            ev, w = r.get("event", {}) or {}, r.get("window", {}) or {}
            out.append({
                "key": key, "experiment": r.get("experiment"), "model": r.get("model"), "horizon_s": r.get("horizon_s"),
                "n_windows": w.get("n_windows"), "n_positive_windows": w.get("n_positive"), "prevalence": w.get("prevalence"),
                "average_precision": w.get("average_precision"), "average_precision_ci": _ci(r, "average_precision"),
                "roc_auc": w.get("roc_auc"), "brier": w.get("brier"), "brier_prevalence_reference": w.get("brier_prevalence_reference"),
                "n_events_evaluable": ev.get("n_events_evaluable"), "n_events_detected": ev.get("n_events_detected"),
                "event_sensitivity": ev.get("event_sensitivity"), "event_sensitivity_ci": _ci(r, "event_sensitivity"),
                "n_false_alerts": ev.get("n_false_alerts"), "monitored_hours": ev.get("monitored_hours"),
                "false_alerts_per_hour": ev.get("false_alerts_per_hour"), "false_alerts_per_hour_ci": _ci(r, "false_alerts_per_hour"),
                "median_lead_s": _get(ev, "lead_time_s", "median"), "median_lead_s_ci": _ci(r, "median_lead_s"),
                "n_leads": ev.get("n_leads"),
                "precision": _get(w, "at_selected_thresholds", "precision"), "recall": _get(w, "at_selected_thresholds", "recall"),
                "n_participants": r.get("n_participants"), "n_participants_with_evaluable_events": r.get("n_participants_with_evaluable_events"),
            })
        return out

    def detail(self, experiment: str, model: str, horizon_s: float) -> dict | None:
        r = self.entry(experiment, model, horizon_s)
        if r is None:
            return None
        return {
            "key": self.result_key(experiment, model, horizon_s),
            "summary": next((x for x in self.rows() if x["key"] == self.result_key(experiment, model, horizon_s)), None),
            "per_fold": r.get("per_fold") or [], "per_participant": r.get("per_participant") or [],
            "lead_time_s": _get(r, "event", "lead_time_s"), "calibration": r.get("calibration") or [],
            "thresholds_per_fold": r.get("thresholds") or [], "at_selected_thresholds": _get(r, "window", "at_selected_thresholds"),
            "n_redundant_alerts": _get(r, "event", "n_redundant_alerts"),
        }

    def evaluation_context(self) -> dict:
        """Design facts as saved by Phase 2 (only keys that exist; no defaults are invented)."""
        d = self.data or {}
        cfg = _get(d, "config", "phase2") or {}
        ws = d.get("window_summary") or {}
        return {"experiment_id": d.get("experiment_id"), "config_hash": d.get("config_hash"),
                "split": cfg.get("split"), "threshold_policy": cfg.get("threshold"), "windowing": cfg.get("windowing"),
                "n_events_total": ws.get("n_events_total"), "n_events_by_task": ws.get("n_events_by_task"),
                "events_touching_recording_end": ws.get("events_touching_recording_end"),
                "n_participants": next(iter(self.rows()), {}).get("n_participants") if self.rows() else None,
                "model_selection": d.get("selection")}

    def calibration_status(self, experiment: str, model: str, horizon_s: float) -> dict:
        """Describe calibration only from what was saved. `validated` is False unless the saved bins show agreement, which
        Phase 2 never claims; the dashboard therefore always describes scores as uncalibrated."""
        r = self.entry(experiment, model, horizon_s)
        bins = (r or {}).get("calibration") or []
        w = (r or {}).get("window") or {}
        return {"n_bins": len(bins), "brier": w.get("brier"), "brier_prevalence_reference": w.get("brier_prevalence_reference"),
                "validated_calibrated": False,
                "reason": "Phase 2 did not validate probability calibration; scores are model outputs, not event probabilities."}

    def report_text(self) -> str | None:
        if self.report_path and self.report_path.is_file():
            return self.report_path.read_text()
        return None


def load_performance(settings4, phase2_settings) -> PerformanceReports:
    pc = settings4["paths"]
    rd = phase2_settings.report_dir
    mp = settings4.resolve(pc["phase2_metrics"]) if pc.get("phase2_metrics") else rd / "phase2_metrics.json"
    rp = settings4.resolve(pc["phase2_report"]) if pc.get("phase2_report") else rd / "phase2_results_report.md"
    return PerformanceReports(mp, rp)


__all__ = ["PerformanceReports", "load_performance", "TwinError"]
