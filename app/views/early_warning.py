"""Early Warning: the latest forecast returned by the Phase 2 model through the Phase 3 engine, and its recent history."""
from __future__ import annotations

import streamlit as st

from components.common import banner, dataset_guard, disclaimer, fmt_score, fmt_time, live_page, no_session
from components.prediction_cards import forecast_card, forecast_details
from components.sensor_charts import risk_figure
from state import validate_selection
from theme import badge_html


def render_live(ctx) -> None:
    s4, svc = ctx.s4, ctx.svc
    sess = ctx.session()
    if sess is None:
        no_session(ctx, "the early-warning panel")
        return
    v = svc.view(sess)
    forecast_card(ctx, v)
    forecast_details(ctx, v)
    wp = v["warning_policy"]
    st.markdown(badge_html(s4, "warning", wp["state"]) + " &nbsp; " + s4.style("warning", wp["state"])["help"], unsafe_allow_html=True)
    frame = svc.forecast_frame(sess)
    if len(frame) == 0:
        st.info("No forecast has been computed yet (the twin is " + v["twin"]["status"].lower().replace("_", " ") + "). This is not low risk.")
    else:
        thr = next((x for x in frame["threshold"].dropna()), None)
        st.plotly_chart(risk_figure(ctx, frame, thr, warnings=svc.warnings(sess)), use_container_width=True, key="ew_fig")
        st.caption("Each point is a forecast computed by the trained model from the trailing window only. Red shading = warning periods from the warning manager.")
        st.markdown("**Recent forecasts**")
        show = frame.tail(s4["tables"]["recent_forecasts"]).iloc[::-1][["prediction_time", "probability", "threshold", "above_threshold", "data_quality", "window_start_time", "window_end_time"]].rename(columns={"probability": "score (uncalibrated)"})
        st.dataframe(show, hide_index=True, use_container_width=True)
    if v["data_quality"]["quality_status"] not in ("ok", "none"):
        st.warning(f"Data quality: {v['data_quality']['quality_status']} - {v['data_quality']['detail']}")
    banner(s4["text"]["warning_caveat"])


def render(ctx) -> None:
    st.title("Early Warning")
    disclaimer(ctx)
    if not dataset_guard(ctx):
        return
    validate_selection(ctx)
    live_page(ctx, render_live)
