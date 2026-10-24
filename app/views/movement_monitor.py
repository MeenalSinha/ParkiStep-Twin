"""Movement Monitor: observations the engine accepted, with gaps and missing values left visible."""
from __future__ import annotations

import streamlit as st

from components.common import banner, dataset_guard, disclaimer, fmt_time, live_page, no_session
from components.sensor_charts import movement_figure
from state import selection, validate_selection
from theme import badge_html


def render_live(ctx) -> None:
    s4, svc = ctx.s4, ctx.svc
    sess = ctx.session()
    if sess is None:
        no_session(ctx, "the movement monitor")
        return
    cat = svc.channel_info()
    all_signals = list(dict.fromkeys(c["signal"] for c in cat))
    all_positions = list(dict.fromkeys(c["position"] for c in cat))
    c1, c2, c3 = st.columns([2, 2, 1])
    signals = c1.multiselect("Signals", all_signals, default=[s for s in s4["plots"]["default_signals"] if s in all_signals], key="mm_signals")
    positions = c2.multiselect("Sensor positions", all_positions, default=all_positions, key="mm_positions")
    mode = c3.radio("Range", ["Recent", "Whole replay"], horizontal=True, key="mm_range")
    last_s = s4["plots"]["history_s"] if mode == "Recent" else None
    res = svc.observation_traces(sess, last_s=last_s, signals=signals or None, positions=positions or None)
    v = svc.view(sess, 0)
    obs, dq = v["observed"], v["data_quality"]
    c = st.columns(4)
    c[0].metric("Recording", str(obs["session_id"] or v["session"]["recording_id"]))
    c[1].metric("Latest timestamp", fmt_time(s4, obs["latest_observation_ts"]))
    c[2].metric("Sensor-quality status", dq["quality_status"])
    c[3].metric("Window coverage", f"{dq['coverage_fraction']:.0%}")
    if not res["traces"]:
        st.info("No accepted observations in the selected range yet.")
        return
    warns = [w for w in svc.warnings(sess)]
    t0 = res["traces"][0]["x"]
    lo = float(t0[0]) if len(t0) else None
    shown = [w for w in warns if lo is None or (w.get("resolved_at") or w["activated_at"]) >= lo]
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
    st.title("Movement Monitor")
    disclaimer(ctx)
    if not dataset_guard(ctx):
        return
    validate_selection(ctx)
    live_page(ctx, render_live)
