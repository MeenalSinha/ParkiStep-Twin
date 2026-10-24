"""Warning Center helpers. Warnings are displayed exactly as the Phase 3 warning manager recorded them."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from components.common import fmt_score, fmt_time
from theme import badge_html


def warnings_frame(ctx, warnings: list[dict]) -> pd.DataFrame:
    s4 = ctx.s4
    rows = []
    for w in warnings:
        rows.append({"warning_id": w["warning_id"], "status": w["status"], "activated_at_s": w["activated_at"], "resolved_at_s": w.get("resolved_at"),
                     "max_score": w.get("max_score"), "n_qualifying_forecasts": w.get("n_qualifying_forecasts"), "threshold": w.get("threshold"),
                     "horizon_s": w.get("horizon_s"), "activating_prediction": w.get("activation_prediction_id"),
                     "acknowledged": bool(w.get("acknowledged_at")), "resolution_reason": w.get("resolution_reason")})
    return pd.DataFrame(rows).head(s4["tables"]["max_rows"])


def warning_detail(ctx, svc, session, warning: dict) -> None:
    s4 = ctx.s4
    c1, c2, c3 = st.columns(3)
    c1.metric("Activated (observation clock)", fmt_time(s4, warning["activated_at"]))
    c2.metric("Resolved", fmt_time(s4, warning.get("resolved_at")))
    c3.metric("Status", warning["status"])
    st.write({k: warning.get(k) for k in ("warning_id", "model_id", "horizon_s", "threshold", "threshold_source", "reason", "resolution_reason",
                                          "activation_prediction_id", "last_qualifying_prediction_id", "n_qualifying_forecasts", "max_score",
                                          "acknowledged_at", "acknowledged_by") if k in warning})
    with st.expander("Lifecycle (append-only)"):
        st.write(warning.get("lifecycle") or [])
    pid = warning.get("activation_prediction_id")
    pred = svc.prediction_by_id(session, pid) if pid else None
    st.markdown("**Prediction behind this warning**")
    if pred is None:
        st.caption("The prediction record is no longer in the bounded history (retention.prediction_history_max).")
    else:
        st.write({"score": fmt_score(s4, pred["probability"]), "prediction_time_s": pred["prediction_time"], "window": [pred["window_start_time"], pred["window_end_time"]],
                  "threshold": pred["threshold"], "data_quality": pred["data_quality"], "model_id": pred["model_id"]})
