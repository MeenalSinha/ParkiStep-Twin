"""Replay Analysis: choose a recording, run the replay through the engine, then review it retrospectively."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from components.common import banner, dataset_guard, disclaimer, empty_state, fmt_pct, fmt_time, live_page, page_header
from components.replay_controls import control_bar, model_summary, progress_panel, selection_panel
from components.sensor_charts import movement_figure, risk_figure
from parkistep.dashboard.analysis import LABELS
from parkistep.dashboard.replay_session import SessionStatus
from state import selection, validate_selection


def _setup(ctx) -> None:
    sess = ctx.session()
    live = sess is not None and sess.status in (SessionStatus.RUNNING, SessionStatus.PAUSED)
    st.subheader("1. Choose what to replay")
    if live:
        st.caption("Selection is locked while a replay is running or paused. Stop it to choose another recording.")
    sel = selection_panel(ctx)
    if sel.get("model_key"):
        model_summary(ctx, sel["model_key"])
    if ctx.s4["selection"]["show_annotation_counts_before_replay"] is False:
        st.caption("Annotated events are hidden until the replay has ended: they are never part of the live setup or of the model input.")
    st.subheader("2. Run")
    control_bar(ctx, sel)
    progress_panel(ctx)


def _retrospective(ctx, sess) -> None:
    s4, svc = ctx.s4, ctx.svc
    summary = sess.summary
    st.subheader("3. Retrospective analysis")
    banner(s4["text"]["retrospective_note"])
    ret = svc.retrospective(sess)
    c = summary["counts"]
    cols = st.columns(5)
    cols[0].metric("Observations processed", f"{c['observations_processed']:,}")
    cols[1].metric("Rejected", f"{sum(c['rejected'].values()):,}")
    cols[2].metric("Forecasts", f"{c['predictions']:,}")
    cols[3].metric("Warnings", f"{c['warnings_activated']:,}")
    unav = c.get("forecasts_unavailable") or {}
    if unav:
        st.info("Forecast unavailable for some windows (no score was produced, none is shown as zero risk): "
                + "; ".join(f"{str(k).replace('_', ' ')} x {v:,}" for k, v in sorted(unav.items(), key=lambda kv: -kv[1])) + ".")
    cols[4].metric("Future-data violations", f"{summary['future_data_check']['violations']}")
    st.caption(f"Determinism digest `{summary['determinism_digest'][:16]}...` - identical data and configuration reproduce it. {summary['in_sample_note']}")
    if summary.get("partial_replay"):
        st.warning("The replay was stopped early: the comparison covers only the part of the recording that was replayed.")
    if ret is None:
        empty_state("Retrospective comparison unavailable", "Ground-truth comparison is disabled or the model was unavailable.", None)
        return
    t = ret["totals"]
    m = st.columns(4)
    m[0].metric("Annotated events", t["annotated_events"])
    m[1].metric("Detected early", f"{t['events_detected']} / {t['annotated_events']}", help="Events with a valid warning before onset (Phase 2 scoring rules).")
    m[2].metric("Valid / false warnings", f"{t['valid_early_warnings']} / {t['false_warnings']}")
    m[3].metric("Median warning lead", fmt_time(s4, t["median_lead_s"]))
    fa = t["false_warnings_per_hour"]
    fa_txt = "n/a" if fa is None else f"{fa:.1f}"
    st.caption(f"{t['warnings_excluded_overlap_freezing']} warning(s) were excluded from scoring because their input window overlapped an annotated episode (Phase 2 rule); "
               f"{t['warnings_excluded_censored_future']} because the forecast horizon ran past the end of the recording. "
               f"False warnings per hour: {fa_txt}, from only {t['scored_monitored_hours'] * 3600:.0f} s of scored time ({t['scored_monitored_hours']:.3f} h) - "
               "a very short denominator, so this is not a meaningful rate.")
    events = ret.get("annotated_events_table", [])
    warns = svc.warnings(sess)
    frame = svc.forecast_frame(sess, 10 ** 9)
    st.markdown("**What the system knew, what it predicted, what the recording later showed**")
    tr = svc.observation_traces(sess, last_s=None)
    st.plotly_chart(movement_figure(ctx, tr["traces"], warnings=warns, events=events), use_container_width=True, key="ra_move")
    thr = next((x for x in frame["threshold"].dropna()), None) if len(frame) else None
    st.plotly_chart(risk_figure(ctx, frame, thr, warnings=warns, events=events), use_container_width=True, key="ra_risk")
    st.caption("Red shading: warning periods (model output). Teal shading: annotated freezing episodes (ground truth, retrospective only).")
    left, right = st.columns(2)
    with left:
        st.markdown("**Annotated events**")
        ev = pd.DataFrame(ret["events"])
        if len(ev):
            ev["outcome"] = ev.apply(lambda r: "detected before onset" if r["detected"] else ("no forecast window before onset" if not r["forecast_window_before_onset"] else "missed"), axis=1)
            st.dataframe(ev[["event_id", "onset_t", "outcome", "warning_lead_s"]], hide_index=True, use_container_width=True)
        else:
            st.caption("No annotated freezing events in this recording.")
    with right:
        st.markdown("**Warning timing relative to annotated onsets**")
        wt = pd.DataFrame(ret["warning_timing"])
        if len(wt):
            st.dataframe(wt[["warning_id", "activated_at", "label", "event_id", "lead_s"]], hide_index=True, use_container_width=True)
            st.caption("Only warnings classed 'Before an annotated onset' can be early warnings; a detection after onset is not counted as one. Descriptive timing only: the official counts above come from the Phase 2 scoring rules.")
        else:
            st.caption("No warnings were raised.")
    st.caption(ret["note"])


def render_live(ctx) -> None:
    _setup(ctx)
    sess = ctx.session()
    if sess is not None and sess.is_finished and sess.status != SessionStatus.FAILED and sess.summary:
        _retrospective(ctx, sess)


def render(ctx) -> None:
    page_header(ctx, "Interactive Replay & Retrospective Analysis", "SIMULATION & VERIFICATION", "Replay real wearable sensor recordings through the digital twin with ground-truth withholding and post-session evaluation.", "#0284C7")
    if not dataset_guard(ctx):
        return
    validate_selection(ctx)
    live_page(ctx, render_live)


