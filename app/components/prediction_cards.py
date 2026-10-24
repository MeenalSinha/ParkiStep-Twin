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
    st.markdown(f"<div style='font-size:0.75rem;font-weight:600;color:#64748B;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:6px;'>{s4['text']['score_label']}</div>", unsafe_allow_html=True)
    if cur is not None:
        above = cur["above_threshold"]
        prob = cur["probability"]
        thr = cur["threshold"]
        prob_pct = min(100.0, max(0.0, prob * 100))
        thr_pct = min(100.0, max(0.0, thr * 100))
        bar_color = "#DC2626" if above else "#0D9488"
        b_markup = badge_html(s4, "forecast", fs)
        status_text = "AT OR ABOVE OPERATING THRESHOLD" if above else "BELOW OPERATING THRESHOLD"
        status_color = "#DC2626" if above else "#0D9488"

        st.markdown(f"""
        <div class="pk-card" style="border-left: 4px solid {bar_color};">
            <div class="pk-card-header">
                <h4>Estimated Freezing Risk Score</h4>
                {b_markup}
            </div>
            <div style="display:flex;align-items:baseline;gap:12px;margin:6px 0;">
                <div class="pk-big" style="color:{bar_color};">{fmt_score(s4, prob)}</div>
                <div style="font-size:0.85rem;font-weight:600;color:{status_color};letter-spacing:0.02em;">{status_text}</div>
            </div>
            <div style="position:relative;background:#E2E8F0;height:8px;border-radius:4px;margin:10px 0 8px 0;overflow:hidden;">
                <div style="background:{bar_color};width:{prob_pct:.1f}%;height:100%;border-radius:4px;transition:width 0.3s ease;"></div>
            </div>
            <div style="display:flex;justify-content:space-between;font-size:0.75rem;color:#64748B;">
                <span>0.000</span>
                <span style="font-weight:600;color:#0F172A;">Operating Threshold: {fmt_score(s4, thr)} ({cur['threshold_source']})</span>
                <span>1.000</span>
            </div>
        </div>
        """, unsafe_allow_html=True)
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
