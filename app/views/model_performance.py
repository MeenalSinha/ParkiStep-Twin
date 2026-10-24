"""Model Performance: participant-grouped out-of-fold results read from the saved Phase 2 outputs - nothing hard-coded."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from components.common import banner, disclaimer, empty_state, fmt_score, page_header
from components.performance_tables import fold_table, lead_table, metric_figure, participant_table, summary_table
from state import selection, validate_selection


def render(ctx) -> None:
    s4, svc = ctx.s4, ctx.svc
    page_header(ctx, "Model Validation & Retrospective Performance", "OFFLINE VALIDATION", "Participant-grouped out-of-fold cross-validation metrics and clinical benchmark evaluation.", "#0284C7")
    perf = svc.performance()
    if not perf.available:
        st.error("Evaluation results unavailable")
        st.write(perf.problem)
        return
    validate_selection(ctx)
    banner(s4["text"]["performance_note"])
    st.warning("Research prototype - not a clinically validated diagnostic and not a medical device. These figures describe a small cohort recorded in structured "
               "laboratory tasks; weak performance below is a property of the model, not of the dashboard.")
    ctxd = perf.evaluation_context()
    rows = perf.rows()
    n_part = rows[0]["n_participants"] if rows else None
    sp = ctxd.get("split") or {}
    st.caption(f"Experiment `{perf.experiment_id}` - {n_part} participants - split: {sp.get('strategy', 'see report')} - "
               f"{ctxd.get('n_events_total')} annotated events in total; the 'evaluable events' column is the per-row denominator. "
               "Intervals are 95% participant-bootstrap intervals saved with the results.")
    sel = selection()
    d = svc.model_description(sel["model_key"]) if sel["model_key"] else {"available": False}
    sel_key = None
    if d.get("available"):
        model_name = d["identity"]["model_id"].split("/")[-1]
        sel_key = perf.result_key(d["experiment"], model_name, d["horizon_s"])
        st.markdown(f"**Currently selected model:** `{d['identity']['model_id']}` - horizon {d['horizon_s']:g} s")
        det = perf.detail(d["experiment"], model_name, d["horizon_s"])
        if det is None:
            empty_state("No saved evaluation for the selected model", f"The saved results do not include {sel_key}.", "Nothing was substituted.")
        else:
            r = det["summary"]
            c = st.columns(4)
            c[0].metric("Average precision", f"{r['average_precision']:.3f}" if r["average_precision"] is not None else "n/a",
                        help=f"Prevalence of positive windows is {r['prevalence']:.3f}; a no-skill model scores about that.")
            c[1].metric("Event sensitivity", f"{r['event_sensitivity']:.2f}" if r["event_sensitivity"] is not None else "n/a", help=f"{r['n_events_detected']} of {r['n_events_evaluable']} evaluable events")
            c[2].metric("False alerts / hour", f"{r['false_alerts_per_hour']:.1f}" if r["false_alerts_per_hour"] is not None else "n/a", help=f"{r['n_false_alerts']} alerts over {r['monitored_hours']:.2f} h monitored")
            c[3].metric("Median warning lead", f"{r['median_lead_s']:.2f} s" if r["median_lead_s"] is not None else "n/a", help=f"{r['n_leads']} detected events")
            cal = d["calibration"]
            st.caption(f"Calibration: {cal['reason']} Brier {fmt_score(s4, cal.get('brier'))} vs. {fmt_score(s4, cal.get('brier_prevalence_reference'))} for always predicting the prevalence.")
            tabs = st.tabs(["Fold-level variation", "Lead-time distribution", "Per participant", "Calibration bins"])
            with tabs[0]:
                st.dataframe(fold_table(det), hide_index=True, use_container_width=True)
                st.caption("Each fold holds out different participants; the spread between folds is the between-participant variability.")
            with tabs[1]:
                lt = lead_table(det)
                st.dataframe(lt, hide_index=True, use_container_width=True) if len(lt) else st.caption("No detected events, so no lead-time distribution exists.")
            with tabs[2]:
                st.dataframe(participant_table(det), hide_index=True, use_container_width=True)
            with tabs[3]:
                cb = pd.DataFrame(det["calibration"])
                st.dataframe(cb, hide_index=True, use_container_width=True) if len(cb) else st.caption("No calibration bins saved.")
    else:
        st.info("No model is selected or the selected model is unavailable; the full table below is still the saved evaluation.")
    st.subheader("All evaluated experiments")
    horizons = sorted({r["horizon_s"] for r in rows if r["horizon_s"] is not None})
    pick = st.radio("Horizon (s)", ["all", *horizons], horizontal=True, key="mp_h")
    shown = [r for r in rows if pick == "all" or r["horizon_s"] == pick]
    st.dataframe(summary_table(ctx, shown, sel_key), hide_index=True, use_container_width=True)
    c1, c2 = st.columns(2)
    if shown:
        c1.plotly_chart(metric_figure(ctx, shown, "event_sensitivity", "event_sensitivity_ci", "Event sensitivity (95% CI)"), use_container_width=True, key="mp_sens")
        c2.plotly_chart(metric_figure(ctx, shown, "false_alerts_per_hour", "false_alerts_per_hour_ci", "False alerts per hour (95% CI)"), use_container_width=True, key="mp_fa")
    st.caption("Dummy models are chance-level baselines. False alerts per hour use the monitored hours of that row as denominator and carry wide intervals because there are few events.")
    with st.expander("In-sample replay vs. out-of-sample evaluation"):
        st.write("A replay of a dataset recording uses a saved model that was fitted on all participants, including the replayed one. Its retrospective counts (Replay Analysis) are an "
                 "in-sample software demonstration. Only the participant-grouped results on this page estimate behaviour on new participants.")
    rep = perf.report_text()
    if rep:
        with st.expander("Full Phase 2 results report"):
            st.markdown(rep)
