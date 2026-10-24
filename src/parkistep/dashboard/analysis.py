"""Retrospective helpers: relate warnings to annotated onsets AFTER a replay (descriptive; no alert decisions).

The authoritative detected/false/valid counts are produced by Phase 3 `ReplayRunner.compare` (Phase 2 scoring rules). The
function below only explains the timing of each warning relative to the annotations so the timeline can distinguish a
pre-onset warning from a detection after an episode had already started.
"""
from __future__ import annotations

PRE_ONSET = "pre_onset_within_horizon"
DURING = "during_annotated_event"
OVERLAP = "input_window_overlaps_event"
NONE_IN_HORIZON = "no_onset_within_horizon"
LABELS = {
    PRE_ONSET: "Before an annotated onset (within the forecast horizon)",
    DURING: "After onset - inside an annotated episode (not an early warning)",
    OVERLAP: "Input window overlapped an annotated episode (excluded from scoring by Phase 2 rules)",
    NONE_IN_HORIZON: "No annotated onset within the forecast horizon",
}


def classify_warnings(warnings: list[dict], events: list[dict], horizon_s: float, input_window_s: float) -> list[dict]:
    """Classify each warning by its activation time `activated_at` against annotated events (onset_t, end_t, recording)."""
    out = []
    for w in warnings:
        t = float(w["activated_at"])
        rid = str(w.get("session_id", "")).split("|")[-1]
        evs = [e for e in events if str(e.get("recording", rid)) == rid or "recording" not in e]
        during = next((e for e in evs if e["onset_t"] <= t <= e["end_t"]), None)
        overlap = next((e for e in evs if e["onset_t"] <= t and e["end_t"] >= t - input_window_s), None)
        ahead = sorted((e for e in evs if t < e["onset_t"] <= t + horizon_s), key=lambda e: e["onset_t"])
        if during is not None:
            cat, ev, lead = DURING, during, None
        elif overlap is not None:
            cat, ev, lead = OVERLAP, overlap, None
        elif ahead:
            cat, ev, lead = PRE_ONSET, ahead[0], float(ahead[0]["onset_t"] - t)
        else:
            cat, ev, lead = NONE_IN_HORIZON, None, None
        out.append({"warning_id": w["warning_id"], "activated_at": t, "category": cat, "label": LABELS[cat],
                    "event_id": None if ev is None else ev.get("event_id"), "lead_s": lead})
    return out
