"""Overview: who is selected, what data exist, what the model reports, and whether data/model issues exist."""
from __future__ import annotations

import streamlit as st

from components.common import badge, banner, card, dataset_guard, disclaimer, empty_state, fmt_score, fmt_time, live_page
from components.prediction_cards import forecast_card
from state import selection, validate_selection
from theme import badge_html


def render_live(ctx) -> None:
    s4, svc = ctx.s4, ctx.svc
    sess = ctx.session()
    sel = selection()
    if sess is None:
        card("Data mode", "No replay running", "No recording is being fed to a twin in this session.", badge_html(s4, "data_mode", "idle"))
        empty_state("Nothing monitored", "No sensor stream is active; no forecast exists. This is not low risk.", "Use **Replay Analysis** to start a replay of a recorded session.")
        st.markdown(f"Selected patient: **{sel['patient_id'] or 'none'}**, recording: **{sel['recording_id'] or 'none'}**.")
        stored = svc.stored_twins()
        if stored:
            st.markdown("**Stored twins (historical, not live):** " + ", ".join(f"patient {t['patient_id']} ({t['status']}, updated {t['updated_at']})" for t in stored))
        return
    v = svc.view(sess)
    ms, tw, obs, dq, cc, wp = v["model_score"], v["twin"], v["observed"], v["data_quality"], v["clinical_context"], v["warning_policy"]
    mode = v["data_mode"]
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        card("Patient", f"Patient {tw['patient_id']}", f"clinical record: {cc['status']}", badge_html(s4, "data_mode", mode))
    with c2:
        card("Twin status", s4.style("twin", tw["status"])["label"], tw["status_reason"], badge_html(s4, "twin", tw["status"]))
    with c3:
        card("Latest sensor timestamp", fmt_time(s4, obs["latest_observation_ts"]), f"recording {obs['session_id'] or v['session']['recording_id']} - "
             f"{(obs['n_accepted_session'] or v['session']['observations_processed']):,} accepted")
    with c4:
        md = svc.model_description(sess.label.get("model_key"))
        card("Model", f"{md['horizon_s']:g} s horizon" if md.get("available") else "unavailable",
             md["identity"]["model_id"] if md.get("available") else md.get("problem", ""))
    left, right = st.columns([3, 2])
    with left:
        forecast_card(ctx, v)
    with right:
        w = wp["active_warning"]
        card("Warning status", s4.style("warning", wp["state"])["label"], (f"since {fmt_time(s4, w['activated_at'])}" if w else f"{wp['n_warnings']} warning(s) so far"),
             badge_html(s4, "warning", wp["state"]))
        q = dq["quality_status"]
        card("Data quality", q, f"window coverage {dq['coverage_fraction']:.0%}, {dq['buffered_samples']}/{dq['required_samples']} samples buffered" if dq["required_samples"] else "")
        if cc["influences_forecast"] is False:
            st.caption(s4["text"]["sensor_only_clinical_note"])
    if tw["status"] in ("DEGRADED_DATA_QUALITY", "STALE", "ERROR", "MODEL_UNAVAILABLE"):
        st.warning(f"Data/model issue: {tw['status_reason']}")
    if mode == "historical":
        st.info("This replay has ended. What is shown is the final recorded state of the twin, not live monitoring.")


def render(ctx) -> None:
    st.title(ctx.s4["app"]["title"])
    st.caption(ctx.s4["app"]["subtitle"])
    disclaimer(ctx)
    if not dataset_guard(ctx):
        return
    validate_selection(ctx)
    live_page(ctx, render_live)
