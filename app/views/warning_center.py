"""Warning Center: active and historical warnings exactly as recorded by the Phase 3 warning manager."""
from __future__ import annotations

import streamlit as st

from components.common import banner, dataset_guard, disclaimer, empty_state, fmt_time, live_page, no_session
from components.warning_timeline import warning_detail, warnings_frame
from parkistep.dashboard.service import SelectionError
from state import validate_selection
from theme import badge_html


def render_live(ctx) -> None:
    s4, svc = ctx.s4, ctx.svc
    sess = ctx.session()
    if sess is None:
        no_session(ctx, "the warning center")
        return
    v = svc.view(sess, 0)
    wp = v["warning_policy"]
    st.markdown(badge_html(s4, "warning", wp["state"]) + " &nbsp; " + s4.style("warning", wp["state"])["help"], unsafe_allow_html=True)
    with st.expander("Warning policy in force (Phase 3 / saved with the model)"):
        st.write(wp["policy"])
        st.caption(f"Suppressed by cooldown: {wp['suppressed_cooldown']}.")
    warns = svc.warnings(sess)
    if not warns:
        empty_state("No warnings recorded", "The warning policy has not raised a warning in this replay so far. No warning does not mean no freezing episode.", None)
        banner(s4["text"]["warning_caveat"])
        return
    st.dataframe(warnings_frame(ctx, warns), hide_index=True, use_container_width=True)
    ids = [w["warning_id"] for w in warns]
    chosen = st.selectbox("Inspect warning", ids, index=len(ids) - 1, key="wc_pick")
    w = next(x for x in warns if x["warning_id"] == chosen)
    warning_detail(ctx, svc, sess, w)
    if s4["features"]["allow_acknowledge"] and w["status"] == "ACTIVE" and not w.get("acknowledged_at"):
        if st.button("Acknowledge (recorded by the engine)", key="wc_ack"):
            try:
                svc.acknowledge_warning(sess, chosen, by=None)
                st.rerun()
            except SelectionError as e:
                st.error(str(e))
    banner(s4["text"]["warning_caveat"])


def render(ctx) -> None:
    st.title("Warning Center")
    disclaimer(ctx)
    if not dataset_guard(ctx):
        return
    validate_selection(ctx)
    live_page(ctx, render_live)
