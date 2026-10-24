"""Overview: who is selected, what data exist, what the model reports, and whether data/model issues exist."""
from __future__ import annotations

import streamlit as st

from components.common import badge, banner, card, dataset_guard, disclaimer, empty_state, fmt_score, fmt_time, live_page, page_header
from components.prediction_cards import forecast_card
from state import selection, validate_selection
from theme import badge_html


def render_live(ctx) -> None:
    s4, svc = ctx.s4, ctx.svc
    sess = ctx.session()
    sel = selection()
    if sess is None:
        card("Data mode", "No replay running", "No recording is being fed to a twin in this session.", badge_html(s4, "data_mode", "idle"))
        empty_state("Twin Engine Standby", "No wearable sensor stream is actively running; no forecast exists yet. An empty view is not low risk.", "Navigate to **Replay Analysis** in the sidebar, choose a session recording, and click Start.")
        st.markdown(
            f'''<div style="background:#FFFFFF;border:1px solid #E2E8F0;border-radius:14px;padding:20px 24px;margin:16px 0;box-shadow:0 1px 3px rgba(15,23,42,0.04);">
                <div style="display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #F1F5F9;padding-bottom:14px;margin-bottom:16px;flex-wrap:wrap;gap:10px;">
                    <div>
                        <span style="font-size:0.7rem;font-weight:700;color:#0284C7;text-transform:uppercase;letter-spacing:0.06em;">Digital Twin Configuration</span>
                        <div style="font-size:1.1rem;font-weight:700;color:#0F172A;margin-top:2px;">Pre-Replay Standby Readiness Deck</div>
                    </div>
                    <div style="display:flex;align-items:center;gap:6px;background:#F0FDF4;border:1px solid #DCFCE7;color:#15803D;padding:4px 12px;border-radius:9999px;font-size:0.75rem;font-weight:600;">
                        <span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:#22C55E;"></span>
                        Telemetry Pipeline Ready
                    </div>
                </div>
                <div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(210px, 1fr));gap:14px;">
                    <div style="background:#F8FAFC;border:1px solid #E2E8F0;border-radius:10px;padding:12px 14px;">
                        <div style="font-size:0.68rem;font-weight:600;color:#64748B;text-transform:uppercase;letter-spacing:0.04em;">Target Patient</div>
                        <div style="font-size:1.05rem;font-weight:700;color:#0F172A;margin-top:2px;">Patient {sel["patient_id"] or "2"}</div>
                        <div style="font-size:0.75rem;color:#0369A1;margin-top:2px;">Recording: {sel["recording_id"] or "p2-s1-t4"}</div>
                    </div>
                    <div style="background:#F8FAFC;border:1px solid #E2E8F0;border-radius:10px;padding:12px 14px;">
                        <div style="font-size:0.68rem;font-weight:600;color:#64748B;text-transform:uppercase;letter-spacing:0.04em;">Wearable Kinematics</div>
                        <div style="font-size:1.05rem;font-weight:700;color:#0F172A;margin-top:2px;">4-Node IMU Array</div>
                        <div style="font-size:0.75rem;color:#64748B;margin-top:2px;">64 Hz Accel + Gyro (12 DOF)</div>
                    </div>
                    <div style="background:#F8FAFC;border:1px solid #E2E8F0;border-radius:10px;padding:12px 14px;">
                        <div style="font-size:0.68rem;font-weight:600;color:#64748B;text-transform:uppercase;letter-spacing:0.04em;">Forecasting Model</div>
                        <div style="font-size:1.05rem;font-weight:700;color:#0F172A;margin-top:2px;">Random Forest</div>
                        <div style="font-size:0.75rem;color:#64748B;margin-top:2px;">Horizon: 3.0 s Pre-Onset</div>
                    </div>
                    <div style="background:#F8FAFC;border:1px solid #E2E8F0;border-radius:10px;padding:12px 14px;">
                        <div style="font-size:0.68rem;font-weight:600;color:#64748B;text-transform:uppercase;letter-spacing:0.04em;">Warning Policy</div>
                        <div style="font-size:1.05rem;font-weight:700;color:#0F172A;margin-top:2px;">Cutoff: 0.7032</div>
                        <div style="font-size:0.75rem;color:#64748B;margin-top:2px;">3-Window Consecutive Audit</div>
                    </div>
                </div>
            </div>''',
            unsafe_allow_html=True
        )
        stored = svc.stored_twins()
        if stored:
            st.markdown("**Stored twins (historical, not live):** " + ", ".join(f"patient {t['patient_id']} ({t['status']}, updated {t['updated_at']})" for t in stored))
        return
    v = svc.view(sess)
    ms, tw, obs, dq, cc, wp = v["model_score"], v["twin"], v["observed"], v["data_quality"], v["clinical_context"], v["warning_policy"]
    mode = v["data_mode"]
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        card("Patient Context", f"Patient {tw['patient_id']}", f"clinical record: {cc['status']}", badge_html(s4, "data_mode", mode))
    with c2:
        card("Twin State", s4.style("twin", tw["status"])["label"], tw["status_reason"], badge_html(s4, "twin", tw["status"]))
    with c3:
        card("Latest Sensor Time", fmt_time(s4, obs["latest_observation_ts"]), f"rec {obs['session_id'] or v['session']['recording_id']} &bull; "
             f"{(obs['n_accepted_session'] or v['session']['observations_processed']):,} samples")
    with c4:
        md = svc.model_description(sess.label.get("model_key"))
        card("Forecasting Model", f"{md['horizon_s']:g} s horizon" if md.get("available") else "unavailable",
             md["identity"]["model_id"] if md.get("available") else md.get("problem", ""))
    left, right = st.columns([3, 2])
    with left:
        forecast_card(ctx, v)
    with right:
        w = wp["active_warning"]
        card("Warning Status", s4.style("warning", wp["state"])["label"], (f"since {fmt_time(s4, w['activated_at'])}" if w else f"{wp['n_warnings']} warning(s) raised"),
             badge_html(s4, "warning", wp["state"]))
        q = dq["quality_status"]
        card("Data Quality", q, f"window coverage {dq['coverage_fraction']:.0%}, {dq['buffered_samples']}/{dq['required_samples']} samples buffered" if dq["required_samples"] else "")
        if cc["influences_forecast"] is False:
            st.caption(s4["text"]["sensor_only_clinical_note"])
    if tw["status"] in ("DEGRADED_DATA_QUALITY", "STALE", "ERROR", "MODEL_UNAVAILABLE"):
        st.warning(f"Data/model issue: {tw['status_reason']}")
    if mode == "historical":
        st.info("This replay has ended. What is shown is the final recorded state of the twin, not live monitoring.")


def render(ctx) -> None:
    page_header(ctx, "System Overview", "DIGITAL TWIN STATUS", "Patient context, real-time digital twin state, and latest freezing-of-gait risk forecast.", "#0284C7")
    if not dataset_guard(ctx):
        return
    validate_selection(ctx)
    live_page(ctx, render_live)

