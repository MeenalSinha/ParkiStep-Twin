"""Warning Center: active and historical warnings exactly as recorded by the Phase 3 warning manager."""
from __future__ import annotations

import streamlit as st

from components.common import banner, dataset_guard, disclaimer, empty_state, fmt_time, live_page, no_session, page_header
from components.warning_timeline import warning_detail, warnings_frame
from parkistep.dashboard.service import SelectionError
from state import validate_selection
from theme import badge_html


def render_live(ctx) -> None:
    s4, svc = ctx.s4, ctx.svc
    sess = ctx.session()
    if sess is None:
        no_session(ctx, "the warning center")
        st.markdown('''
        <div style="background:#FFFFFF;border:1px solid #E2E8F0;border-radius:12px;padding:20px 24px;margin:16px 0;box-shadow:0 1px 3px rgba(15,23,42,0.03);">
            <div style="display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #F1F5F9;padding-bottom:12px;margin-bottom:14px;flex-wrap:wrap;gap:8px;">
                <div>
                    <span style="font-size:0.68rem;font-weight:700;color:#D97706;text-transform:uppercase;letter-spacing:0.06em;">Warning Policy Matrix</span>
                    <div style="font-size:1.05rem;font-weight:700;color:#0F172A;margin-top:2px;">Clinical Event Dispatch & Acknowledgment Rules</div>
                </div>
                <div style="font-size:0.75rem;background:#FFFBEB;color:#B45309;border:1px solid #FDE68A;padding:4px 10px;border-radius:9999px;font-weight:600;">
                    Policy Armed & Standby
                </div>
            </div>
            <div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(220px, 1fr));gap:12px;">
                <div style="background:#F8FAFC;border:1px solid #E2E8F0;border-radius:10px;padding:12px 14px;">
                    <div style="font-size:0.7rem;font-weight:700;color:#D97706;text-transform:uppercase;">Activation Gate</div>
                    <div style="font-size:0.875rem;font-weight:600;color:#0F172A;margin:4px 0 2px 0;">3 Consecutive Windows &ge; 0.7032</div>
                    <div style="font-size:0.75rem;color:#64748B;">Prevents single-frame noise triggers from firing false audio/visual alerts.</div>
                </div>
                <div style="background:#F8FAFC;border:1px solid #E2E8F0;border-radius:10px;padding:12px 14px;">
                    <div style="font-size:0.7rem;font-weight:700;color:#D97706;text-transform:uppercase;">Cooldown Guard</div>
                    <div style="font-size:0.875rem;font-weight:600;color:#0F172A;margin:4px 0 2px 0;">10.0 s Post-Event Lockout</div>
                    <div style="font-size:0.75rem;color:#64748B;">Prevents duplicate alerts from re-triggering during recovery from a freezing event.</div>
                </div>
                <div style="background:#F8FAFC;border:1px solid #E2E8F0;border-radius:10px;padding:12px 14px;">
                    <div style="font-size:0.7rem;font-weight:700;color:#D97706;text-transform:uppercase;">Resolution Criteria</div>
                    <div style="font-size:0.875rem;font-weight:600;color:#0F172A;margin:4px 0 2px 0;">Score Returns Below Threshold</div>
                    <div style="font-size:0.75rem;color:#64748B;">Automatically closes the active incident when normal rhythmic gait resumes.</div>
                </div>
                <div style="background:#F8FAFC;border:1px solid #E2E8F0;border-radius:10px;padding:12px 14px;">
                    <div style="font-size:0.7rem;font-weight:700;color:#D97706;text-transform:uppercase;">Audit Trail</div>
                    <div style="font-size:0.875rem;font-weight:600;color:#0F172A;margin:4px 0 2px 0;">Deterministic Cryptographic Digest</div>
                    <div style="font-size:0.75rem;color:#64748B;">Every activation timestamp, duration, and acknowledgment is verifiable and reproducible.</div>
                </div>
            </div>
        </div>
        ''', unsafe_allow_html=True)
        return
    v = svc.view(sess, 0)
    wp = v["warning_policy"]
    st.markdown(
        f'<div style="background:#FFFFFF;border:1px solid #E2E8F0;border-radius:10px;padding:12px 16px;margin:8px 0 14px 0;display:flex;align-items:center;gap:12px;">'
        f'{badge_html(s4, "warning", wp["state"])}'
        f'<span style="font-size:0.875rem;color:#334155;font-weight:500;">{s4.style("warning", wp["state"])["help"]}</span>'
        f'</div>',
        unsafe_allow_html=True
    )
    with st.expander("Warning policy in force (Phase 3 / saved with the model)"):
        st.write(wp["policy"])
        st.caption(f"Suppressed by cooldown: {wp['suppressed_cooldown']}.")
    warns = svc.warnings(sess)
    if not warns:
        empty_state("No warnings recorded", "The warning policy has not raised a warning in this replay so far. No warning does not mean no freezing episode.", None)
        banner(s4["text"]["warning_caveat"])
        return
    st.subheader("Warning Log & Chronology")
    st.dataframe(warnings_frame(ctx, warns), hide_index=True, use_container_width=True)
    st.subheader("Warning Audit & Diagnostics")
    ids = [w["warning_id"] for w in warns]
    chosen = st.selectbox("Inspect warning", ids, index=len(ids) - 1, key="wc_pick")
    w = next(x for x in warns if x["warning_id"] == chosen)
    warning_detail(ctx, svc, sess, w)
    if s4["features"]["allow_acknowledge"] and w["status"] == "ACTIVE" and not w.get("acknowledged_at"):
        if st.button("Acknowledge (recorded by the engine)", key="wc_ack", type="primary"):
            try:
                svc.acknowledge_warning(sess, chosen, by=None)
                st.rerun()
            except SelectionError as e:
                st.error(str(e))
    banner(s4["text"]["warning_caveat"])


def render(ctx) -> None:
    page_header(ctx, "Warning Center & Audit Trail", "INCIDENT AUDIT", "Active and historical freezing alerts with trigger metrics, cooldown states, and acknowledgment records.", "#D97706")
    if not dataset_guard(ctx):
        return
    validate_selection(ctx)
    live_page(ctx, render_live)

