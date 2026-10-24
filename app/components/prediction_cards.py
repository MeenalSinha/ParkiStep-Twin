"""Forecast presentation. Missing / stale / historical forecasts are never rendered as low risk."""
from __future__ import annotations

import streamlit as st

from components.common import badge, banner, card, fmt_pct, fmt_score, fmt_time
from theme import badge_html


def forecast_state(view: dict) -> str:
    return view["model_score"]["forecast_status"]


def forecast_card(ctx, view: dict) -> None:
    s4, ms = ctx.s4, view["model_score"]
    fs = forecast_state(view)
    stl = s4.style("forecast", fs)
    cur, last = ms["current_forecast"], ms["latest_computed_forecast"]
    st.markdown(f"##### {s4['text']['score_label']}")
    if cur is not None:
        above = cur["above_threshold"]
        card("Current model score", fmt_score(s4, cur["probability"]),
             f"threshold {fmt_score(s4, cur['threshold'])} ({cur['threshold_source']}) - " + ("at or above threshold" if above else "below threshold"),
             badge_html(s4, "forecast", fs))
    else:
        card("Current model score", "No current forecast", ms.get("forecast_status_reason") or stl["help"], badge_html(s4, "forecast", fs))
        if last is not None:
            st.caption(f"Last computed forecast (**not current**): score {fmt_score(s4, last['probability'])} at observation time "
                       f"{fmt_time(s4, last['prediction_time'])}" + (f", {fmt_time(s4, ms['latest_forecast_age_s'])} before the latest observation"
                                                                       if ms.get("latest_forecast_age_s") is not None else "") + ".")
    banner(s4["text"]["score_caveat"])


def forecast_details(ctx, view: dict) -> None:
    s4, ms = ctx.s4, view["model_score"]
    p = ms["current_forecast"] or ms["latest_computed_forecast"]
    if p is None:
        return
    label = "Forecast details" if ms["is_current"] else "Forecast details (last computed - NOT current)"
    with st.expander(label, expanded=ms["is_current"]):
        c1, c2, c3 = st.columns(3)
        c1.metric("Forecast horizon", fmt_time(s4, p["horizon_s"]))
        c2.metric("Prediction time (observation clock)", fmt_time(s4, p["prediction_time"]))
        c3.metric("Input window", f"{p['window_start_time']:.{s4['display']['time_decimals']}f} - {p['window_end_time']:.{s4['display']['time_decimals']}f} s")
        c1.metric("Data quality of the window", str(p["data_quality"]))
        c2.metric("Model", str(p["model_id"]).split("/")[-2] + " / " + str(p["model_id"]).split("/")[-1])
        c3.metric("Clinical input used", "yes" if p["clinical_used"] else "no")
        if p.get("warnings"):
            st.warning("Window notes: " + "; ".join(map(str, p["warnings"])))
        st.caption(p.get("score_semantics", ""))
