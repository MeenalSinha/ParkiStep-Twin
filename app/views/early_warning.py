"""Early Warning: the latest forecast returned by the Phase 2 model through the Phase 3 engine, and its recent history."""
from __future__ import annotations

import streamlit as st

from components.common import banner, dataset_guard, disclaimer, fmt_score, fmt_time, live_page, no_session, page_header
from components.prediction_cards import forecast_card, forecast_details
from components.sensor_charts import risk_figure
from state import validate_selection
from theme import badge_html


def render_live(ctx) -> None:
    s4, svc = ctx.s4, ctx.svc
    sess = ctx.session()
    if sess is None:
        no_session(ctx, "the early-warning panel")
        st.markdown('''
        <div style="background:#FFFFFF;border:1px solid #E2E8F0;border-radius:12px;padding:20px 24px;margin:16px 0;box-shadow:0 1px 3px rgba(15,23,42,0.03);">
            <div style="display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #F1F5F9;padding-bottom:12px;margin-bottom:14px;flex-wrap:wrap;gap:8px;">
                <div>
                    <span style="font-size:0.68rem;font-weight:700;color:#0284C7;text-transform:uppercase;letter-spacing:0.06em;">Inference Architecture</span>
                    <div style="font-size:1.05rem;font-weight:700;color:#0F172A;margin-top:2px;">Freezing-of-Gait Pre-Impact Forecasting Engine</div>
                </div>
                <div style="font-size:0.75rem;background:#F0F9FF;color:#0369A1;border:1px solid #BAE6FD;padding:4px 10px;border-radius:9999px;font-weight:600;">
                    3.0 s Pre-Onset Horizon
                </div>
            </div>
            <div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(220px, 1fr));gap:12px;">
                <div style="background:#F8FAFC;border:1px solid #E2E8F0;border-radius:10px;padding:12px 14px;">
                    <div style="font-size:0.7rem;font-weight:700;color:#0284C7;text-transform:uppercase;">Input Window</div>
                    <div style="font-size:0.875rem;font-weight:600;color:#0F172A;margin:4px 0 2px 0;">2.0 s Slided Observation Window</div>
                    <div style="font-size:0.75rem;color:#64748B;">128 samples per IMU channel (516 total extracted spectral & statistical features).</div>
                </div>
                <div style="background:#F8FAFC;border:1px solid #E2E8F0;border-radius:10px;padding:12px 14px;">
                    <div style="font-size:0.7rem;font-weight:700;color:#0284C7;text-transform:uppercase;">Target Lead Time</div>
                    <div style="font-size:0.875rem;font-weight:600;color:#0F172A;margin:4px 0 2px 0;">3.0 s Pre-Onset Warning</div>
                    <div style="font-size:0.75rem;color:#64748B;">Trained to alert prior to clinical freezing onset without post-impact leakage.</div>
                </div>
                <div style="background:#F8FAFC;border:1px solid #E2E8F0;border-radius:10px;padding:12px 14px;">
                    <div style="font-size:0.7rem;font-weight:700;color:#0284C7;text-transform:uppercase;">Operating Threshold</div>
                    <div style="font-size:0.875rem;font-weight:600;color:#0F172A;margin:4px 0 2px 0;">Score &ge; 0.7032</div>
                    <div style="font-size:0.75rem;color:#64748B;">Validated operating point from Phase 2 out-of-fold cross-validation.</div>
                </div>
                <div style="background:#F8FAFC;border:1px solid #E2E8F0;border-radius:10px;padding:12px 14px;">
                    <div style="font-size:0.7rem;font-weight:700;color:#0284C7;text-transform:uppercase;">Persistence Gate</div>
                    <div style="font-size:0.875rem;font-weight:600;color:#0F172A;margin:4px 0 2px 0;">3 Consecutive Windows</div>
                    <div style="font-size:0.75rem;color:#64748B;">Suppresses transient sensor spikes to minimize false alerts during turns.</div>
                </div>
            </div>
        </div>
        ''', unsafe_allow_html=True)
        return
    v = svc.view(sess)
    forecast_card(ctx, v)
    forecast_details(ctx, v)
    wp = v["warning_policy"]
    st.markdown(
        f'<div style="background:#FFFFFF;border:1px solid #E2E8F0;border-radius:10px;padding:10px 14px;margin:10px 0 14px 0;display:flex;align-items:center;gap:10px;">'
        f'{badge_html(s4, "warning", wp["state"])}'
        f'<span style="font-size:0.85rem;color:#475569;">{s4.style("warning", wp["state"])["help"]}</span>'
        f'</div>',
        unsafe_allow_html=True
    )
    frame = svc.forecast_frame(sess)
    if len(frame) == 0:
        st.info("No forecast has been computed yet (the twin is " + v["twin"]["status"].lower().replace("_", " ") + "). This is not low risk.")
    else:
        st.subheader("Risk Score Trajectory")
        thr = next((x for x in frame["threshold"].dropna()), None)
        st.plotly_chart(risk_figure(ctx, frame, thr, warnings=svc.warnings(sess)), use_container_width=True, key="ew_fig")
        st.caption("Each point is a forecast computed by the trained model from the trailing window only. Red shading = warning periods from the warning manager.")
        st.subheader("Recent Forecast Windows")
        show = frame.tail(s4["tables"]["recent_forecasts"]).iloc[::-1][["prediction_time", "probability", "threshold", "above_threshold", "data_quality", "window_start_time", "window_end_time"]].rename(columns={"probability": "score (uncalibrated)"})
        st.dataframe(show, hide_index=True, use_container_width=True)
    if v["data_quality"]["quality_status"] not in ("ok", "none"):
        st.warning(f"Data quality: {v['data_quality']['quality_status']} - {v['data_quality']['detail']}")
    banner(s4["text"]["warning_caveat"])


def render(ctx) -> None:
    page_header(ctx, "Early Warning & Forecasting", "PREDICTIVE ENGINE", "Advance freezing-of-gait risk forecast and temporal alert trajectory.", "#0284C7")
    if not dataset_guard(ctx):
        return
    validate_selection(ctx)
    live_page(ctx, render_live)

