"""Selection and control widgets for the interactive replay. Every action is a service call; no state is faked here."""
from __future__ import annotations

import streamlit as st

from components.common import badge, banner, empty_state, fmt_time
from parkistep.dashboard.replay_session import SessionStatus
from parkistep.dashboard.service import SelectionError
from parkistep.twin.errors import ModelUnavailableError, TwinError
from state import selection, set_sel
from theme import badge_html


def _fmt_speed(x) -> str:
    return "as fast as possible" if x == "max" else f"{x}x real time"


def selection_panel(ctx) -> dict:
    svc, sel = ctx.svc, selection()
    patients = svc.list_patients()
    if not patients:
        empty_state("No patients available", "The dataset contains no replayable patients.", "Check the Phase 1 outputs and configs/datasets.yaml.")
        return sel
    ids = [p["patient_id"] for p in patients]
    c1, c2, c3 = st.columns([1, 2, 2])
    c1.selectbox("Patient", ids, index=ids.index(sel["patient_id"]) if sel["patient_id"] in ids else 0, key="w_patient",
                 on_change=set_sel, args=("patient_id", "w_patient"),
                 format_func=lambda i: f"{i}" + ("" if next(p for p in patients if p['patient_id'] == i)["has_clinical_record"] else " (no clinical record)"))
    sel["patient_id"] = st.session_state.get("w_patient", ids[0])
    recs = svc.list_recordings(sel["patient_id"])
    if not recs:
        c2.warning("This patient has no eligible recordings.")
        return sel
    rids = [r["recording_id"] for r in recs]
    if sel["recording_id"] not in rids:
        sel["recording_id"] = svc.default_recording(sel["patient_id"], ctx.s4["selection"]["default_recording_policy"]
                                                    if ctx.s4["selection"]["default_recording_policy"] != "none" else "phase3_selection") or rids[0]
    c2.selectbox("Recording", rids, index=rids.index(sel["recording_id"]), key="w_recording", on_change=set_sel, args=("recording_id", "w_recording"),
                 format_func=lambda i: (lambda r: f"{r['recording_id']} - {r['task_label'] or 'task ' + str(r['task_id'])} - {r['duration_s']:.0f} s")(next(r for r in recs if r['recording_id'] == i)))
    sel["recording_id"] = st.session_state.get("w_recording", rids[0])
    models = svc.list_models()
    if not models:
        c3.error("No model artifacts found.")
        return sel
    keys = [m["key"] for m in models]
    c3.selectbox("Model", keys, index=keys.index(sel["model_key"]) if sel["model_key"] in keys else 0, key="w_model", on_change=set_sel, args=("model_key", "w_model"),
                 format_func=lambda k: (lambda m: f"{m['experiment']} / {m['model']} / {m['horizon_s']:g} s horizon" + (" (default)" if m["is_default"] else ""))(next(m for m in models if m["key"] == k)))
    sel["model_key"] = st.session_state.get("w_model", keys[0])
    opts = svc.speed_options()
    st.select_slider("Playback speed", options=opts, value=sel["speed"] if sel["speed"] in opts else opts[0], key="w_speed", format_func=_fmt_speed,
                     help="Multiples of real time. A target: inference time can limit the achieved speed.")
    sel["speed"] = st.session_state.get("w_speed", sel["speed"])
    return sel


def model_summary(ctx, key: str) -> None:
    d = ctx.svc.model_description(key)
    if not d["available"]:
        st.error("Model artifact unavailable")
        st.write(d["problem"])
        st.caption(d["next_step"])
        return
    s4 = ctx.s4
    w, t = d["window"], d["threshold"]
    st.caption(f"Model `{d['identity']['model_id']}` - horizon {d['horizon_s']:g} s - input window {w.get('input_window_s')} s - "
               f"operating threshold {t['value']:.{s4['display']['score_decimals']}f} from `{t['source']}` - "
               + ("uses clinical fields (fusion)" if d["uses_clinical"] else "sensor-only (clinical record is context only)"))


def control_bar(ctx, sel: dict) -> None:
    svc, s4 = ctx.svc, ctx.s4
    sess = svc.current_session()
    status = sess.status if sess else None
    c1, c2, c3, c4, c5 = st.columns([1.4, 1, 1, 1, 1.6])
    can_start = status is None or status in (SessionStatus.COMPLETED, SessionStatus.STOPPED, SessionStatus.FAILED)
    start_lbl = "Start new replay" if sess is None or can_start else "Start"
    if c1.button(start_lbl, type="primary", disabled=not (can_start or status == SessionStatus.READY), key="btn_start"):
        try:
            new = svc.create_session(sel["patient_id"], sel["recording_id"], sel["model_key"])
            new.start()
            st.rerun()
        except (SelectionError, ModelUnavailableError) as e:
            st.error(f"Cannot start the replay: {e}")
        except TwinError as e:
            st.error(f"Engine error: {type(e).__name__}: {e}")
    if c2.button("Pause", disabled=status != SessionStatus.RUNNING, key="btn_pause"):
        sess.pause()
        st.rerun()
    if c3.button("Resume", disabled=status != SessionStatus.PAUSED, key="btn_resume"):
        sess.resume()
        st.rerun()
    if c4.button("Stop", disabled=status not in (SessionStatus.RUNNING, SessionStatus.PAUSED), key="btn_stop"):
        sess.stop()
        st.rerun()
    with c5:
        if sess is not None:
            st.markdown(badge_html(s4, "replay", sess.status.value), unsafe_allow_html=True)


def progress_panel(ctx) -> None:
    sess = ctx.svc.current_session()
    s4 = ctx.s4
    if sess is None:
        empty_state("No replay started", "Choose a patient, recording and model, then press Start.", None)
        return
    p = sess.progress()
    st.progress(min(1.0, p.fraction) if p.fraction is not None else 0.0,
                text=f"{p.offered:,} of {p.total_records:,} observations offered" if p.total_records else f"{p.offered:,} observations offered")
    c = st.columns(6)
    c[0].metric("Recording", p.recording_id)
    c[1].metric("Replay position", fmt_time(s4, p.position_s))
    c[2].metric("Processed / rejected", f"{p.observations_processed:,} / {p.observations_rejected:,}")
    c[3].metric("Forecasts", f"{p.predictions:,}")
    c[4].metric("Warnings (active+closed)", f"{p.warnings_activated:,}")
    c[5].metric("Twin status", p.twin_status or "n/a")
    if p.error:
        st.error(f"Replay failed: {p.error['type']}: {p.error['message']}")
        st.caption("Details were written to the application log. No synthetic data were substituted.")
    if p.note:
        st.caption(p.note)
