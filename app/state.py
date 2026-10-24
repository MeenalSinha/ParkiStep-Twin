"""Selection state kept in Streamlit session state (UI interaction state only; twins live in the service)."""
from __future__ import annotations

import streamlit as st

KEY = "sel"


def selection() -> dict:
    return st.session_state.setdefault(KEY, {"patient_id": None, "recording_id": None, "model_key": None, "speed": None})


def validate_selection(ctx) -> dict:
    """Drop selections that no longer exist in the data (dataset or model set changed) and fill defaults per configuration."""
    sel = selection()
    patients = [p["patient_id"] for p in ctx.svc.list_patients()]
    models = [m["key"] for m in ctx.svc.list_models()]
    if sel["patient_id"] not in patients:
        sel["patient_id"], sel["recording_id"] = None, None
    if sel["patient_id"] is not None and sel["recording_id"] not in [r["recording_id"] for r in ctx.svc.list_recordings(sel["patient_id"])]:
        sel["recording_id"] = None
    if sel["model_key"] not in models:
        sel["model_key"] = None
    if sel["patient_id"] is None and patients and not st.session_state.get("_defaults_applied"):
        d = ctx.svc.default_selection()
        sel["patient_id"], sel["recording_id"] = d["patient_id"], d["recording_id"]
        st.session_state["_defaults_applied"] = True
    if sel["model_key"] is None and models:
        sel["model_key"] = ctx.svc.default_model_key() if ctx.svc.default_model_key() in models else models[0]
    if sel["speed"] is None or sel["speed"] not in ctx.svc.speed_options():
        sel["speed"] = ctx.s4["replay"]["default_speed"] if ctx.s4["replay"]["default_speed"] in ctx.svc.speed_options() else ctx.svc.speed_options()[0]
    return sel


def set_sel(key: str, widget_key: str) -> None:
    selection()[key] = st.session_state[widget_key]
    if key == "patient_id":
        selection()["recording_id"] = None            # a recording belongs to one patient; force a fresh, validated choice
