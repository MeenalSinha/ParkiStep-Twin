"""Movement Monitor: observations the engine accepted, with gaps and missing values left visible."""
from __future__ import annotations

import streamlit as st

from components.common import banner, dataset_guard, disclaimer, fmt_time, live_page, no_session, page_header
from components.sensor_charts import movement_figure
from state import selection, validate_selection
from theme import badge_html


def render_live(ctx) -> None:
    s4, svc = ctx.s4, ctx.svc
    sess = ctx.session()
    if sess is None:
        no_session(ctx, "the movement monitor")
        st.markdown('''
        <div style="background:#FFFFFF;border:1px solid #E2E8F0;border-radius:12px;padding:20px 24px;margin:16px 0;box-shadow:0 1px 3px rgba(15,23,42,0.03);">
            <div style="display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #F1F5F9;padding-bottom:12px;margin-bottom:14px;flex-wrap:wrap;gap:8px;">
                <div>
                    <span style="font-size:0.68rem;font-weight:700;color:#0F766E;text-transform:uppercase;letter-spacing:0.06em;">Sensor Topology</span>
                    <div style="font-size:1.05rem;font-weight:700;color:#0F172A;margin-top:2px;">4-Node Biomechanical IMU Architecture</div>
                </div>
                <div style="font-size:0.75rem;background:#F0FDFA;color:#0F766E;border:1px solid #CCFBF1;padding:4px 10px;border-radius:9999px;font-weight:600;">
                    Synchronized 64 Hz Sampling Rate
                </div>
            </div>
            <div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(220px, 1fr));gap:12px;">
                <div style="background:#F8FAFC;border:1px solid #E2E8F0;border-radius:10px;padding:12px 14px;">
                    <div style="font-size:0.7rem;font-weight:700;color:#0F766E;text-transform:uppercase;">Left Ankle (ankleL)</div>
                    <div style="font-size:0.875rem;font-weight:600;color:#0F172A;margin:4px 0 2px 0;">3-Axis Accel + 3-Axis Gyro</div>
                    <div style="font-size:0.75rem;color:#64748B;">Stance-to-swing phase duration, foot strike acceleration, and cadence.</div>
                </div>
                <div style="background:#F8FAFC;border:1px solid #E2E8F0;border-radius:10px;padding:12px 14px;">
                    <div style="font-size:0.7rem;font-weight:700;color:#0F766E;text-transform:uppercase;">Right Ankle (ankleR)</div>
                    <div style="font-size:0.875rem;font-weight:600;color:#0F172A;margin:4px 0 2px 0;">3-Axis Accel + 3-Axis Gyro</div>
                    <div style="font-size:0.75rem;color:#64748B;">Bilateral step symmetry, high-frequency trembling, and freeze initiation.</div>
                </div>
                <div style="background:#F8FAFC;border:1px solid #E2E8F0;border-radius:10px;padding:12px 14px;">
                    <div style="font-size:0.7rem;font-weight:700;color:#0F766E;text-transform:uppercase;">Lumbar Spine (back)</div>
                    <div style="font-size:0.875rem;font-weight:600;color:#0F172A;margin:4px 0 2px 0;">3-Axis Accel + 3-Axis Gyro</div>
                    <div style="font-size:0.75rem;color:#64748B;">Trunk kinematics, core postural sway, and forward body inclination.</div>
                </div>
                <div style="background:#F8FAFC;border:1px solid #E2E8F0;border-radius:10px;padding:12px 14px;">
                    <div style="font-size:0.7rem;font-weight:700;color:#0F766E;text-transform:uppercase;">Dominant Wrist (wrist)</div>
                    <div style="font-size:0.875rem;font-weight:600;color:#0F172A;margin:4px 0 2px 0;">3-Axis Accel + 3-Axis Gyro</div>
                    <div style="font-size:0.75rem;color:#64748B;">Upper-limb reciprocal arm swing asymmetry and involuntary tremor.</div>
                </div>
            </div>
        </div>
        ''', unsafe_allow_html=True)
        return
    cat = svc.channel_info()
    all_signals = list(dict.fromkeys(c["signal"] for c in cat))
    all_positions = list(dict.fromkeys(c["position"] for c in cat))
    st.markdown("<div style='font-size:0.75rem;font-weight:600;color:#64748B;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:6px;'>Telemetry Channels & Display Scope</div>", unsafe_allow_html=True)
    c1, c2, c3 = st.columns([2, 2, 1])
    signals = c1.multiselect("Signals", all_signals, default=[s for s in s4["plots"]["default_signals"] if s in all_signals], key="mm_signals")
    positions = c2.multiselect("Sensor positions", all_positions, default=all_positions, key="mm_positions")
    mode = c3.radio("Range", ["Recent", "Whole replay"], horizontal=True, key="mm_range")
    last_s = s4["plots"]["history_s"] if mode == "Recent" else None
    res = svc.observation_traces(sess, last_s=last_s, signals=signals or None, positions=positions or None)
    v = svc.view(sess, 0)
    obs, dq = v["observed"], v["data_quality"]
    c = st.columns(4)
    c[0].metric("Active Recording", str(obs["session_id"] or v["session"]["recording_id"]))
    c[1].metric("Observation Clock", fmt_time(s4, obs["latest_observation_ts"]))
    c[2].metric("Sensor Quality", dq["quality_status"].upper())
    c[3].metric("Window Coverage", f"{dq['coverage_fraction']:.0%}")
    if not res["traces"]:
        st.info("No accepted observations in the selected range yet.")
        return
    warns = [w for w in svc.warnings(sess)]
    t0 = res["traces"][0]["x"]
    lo = float(t0[0]) if len(t0) else None
    shown = [w for w in warns if lo is None or (w.get("resolved_at") or w["activated_at"]) >= lo]
    st.subheader("Multi-Axis Kinematic Traces")
    st.plotly_chart(movement_figure(ctx, res["traces"], warnings=shown), use_container_width=True, key="mm_fig")
    cov = res["coverage"]
    st.caption(f"{cov['n']:,} observations in view ({cov['duration_s']:.1f} s). Shaded red = warning periods raised by the warning manager. "
               "Traces are decimated for display only; breaks in a line are real gaps or missing values.")
    if cov["gaps"]:
        st.warning(f"{len(cov['gaps'])} gap(s) in the displayed range, e.g. {cov['gaps'][0]['duration_s']:.2f} s starting at {cov['gaps'][0]['start_s']:.2f} s.")
    if cov["missing_by_channel"]:
        st.warning("Missing values (not imputed): " + ", ".join(f"{k}: {n}" for k, n in cov["missing_by_channel"].items()))
    if res["truncated_store"]:
        st.warning("The plot store reached replay.max_stored_observations; later observations are processed by the twin but not plotted.")
    with st.expander("Data-quality events"):
        st.write(v["data_quality"]["history"][-ctx.s4["tables"]["recent_forecasts"]:])


def render(ctx) -> None:
    page_header(ctx, "Movement & Sensor Telemetry", "BIOMECHANICAL STREAM", "Real-time wearable sensor stream across 4 body placements with continuous data quality audit.", "#0F766E")
    if not dataset_guard(ctx):
        return
    validate_selection(ctx)
    live_page(ctx, render_live)

