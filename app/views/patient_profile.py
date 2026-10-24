"""Patient Profile: the verified historical clinical record, with missing values kept missing."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from components.common import banner, dataset_guard, disclaimer, empty_state, page_header
from components.replay_controls import model_summary
from state import selection, set_sel, validate_selection

STATUS_TEXT = {"recorded": "recorded", "not_recorded": "not recorded", "outside_documented_range": "recorded - outside documented range"}


def render(ctx) -> None:
    s4, svc = ctx.s4, ctx.svc
    page_header(ctx, "Patient Clinical Profile", "CLINICAL BASELINE", "Verified baseline clinical context (demographics, motor scales, NFOG-Q) and model feature compatibility.", "#0F766E")
    if not dataset_guard(ctx):
        return
    sel = validate_selection(ctx)
    patients = svc.list_patients()
    if not patients:
        empty_state("No patients", "The dataset lists no patients.", None)
        return
    ids = [p["patient_id"] for p in patients]
    models = svc.list_models()
    keys = [m["key"] for m in models]
    
    col_p, col_m = st.columns(2)
    with col_p:
        st.selectbox("Patient Context", ids, index=ids.index(sel["patient_id"]) if sel["patient_id"] in ids else 0, key="w_patient_pp", on_change=set_sel, args=("patient_id", "w_patient_pp"))
        sel["patient_id"] = st.session_state.get("w_patient_pp", ids[0])
    with col_m:
        if keys:
            st.selectbox("Model Requirements Context", keys, index=keys.index(sel["model_key"]) if sel["model_key"] in keys else 0, key="w_model_pp",
                         on_change=set_sel, args=("model_key", "w_model_pp"))
            sel["model_key"] = st.session_state.get("w_model_pp", keys[0])
            
    prof = svc.clinical_profile(sel["patient_id"], sel["model_key"])
    if not prof["available"]:
        empty_state("No clinical record", prof["problem"] or "The clinical record is unavailable.", "The twin can still process sensor data; clinical context will be empty, not zero.")
    else:
        st.subheader("Clinical Baseline Attributes")
        rows = [{"Field": f["field"], "Value": ("not recorded" if f["value"] is None else str(f["value"])), "Unit": f["unit"] or "", "Documented range":
                 ("" if not f["documented_range"] else f"{f['documented_range'][0]}-{f['documented_range'][1]}"), "Status": STATUS_TEXT[f["status"]],
                 "Used by selected model": "yes" if f["used_by_selected_model"] else "no"} for f in prof["fields"]]
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        st.caption(f"{prof['n_recorded']} of {prof['n_fields']} fields recorded. 'Not recorded' means the source holds no value - it is not a negative finding and no value was imputed. "
                   f"Source: {prof['provenance'].get('dataset')} (audit {prof['provenance'].get('audit_status')}).")
        if prof["model_uses_clinical"] is False:
            banner(prof["sensor_only_note"] or s4["text"]["sensor_only_clinical_note"])
        elif prof["model_uses_clinical"]:
            st.info("The selected model is a fusion model; the fields marked 'yes' enter its forecast, with the missing-value handling saved with the model.")
    st.subheader("Selected Model: Architecture & Input Requirements")
    d = svc.model_description(sel["model_key"])
    if not d["available"]:
        model_summary(ctx, sel["model_key"])
        return
    c1, c2, c3 = st.columns(3)
    c1.metric("Forecast Horizon", f"{d['horizon_s']:g} s")
    c2.metric("Input Window Duration", f"{d['window'].get('input_window_s')} s")
    c3.metric("Extracted Features", str(d["n_features"] or "n/a"))
    with st.expander("Model Specification & Checksum Provenance"):
        st.write({"model_id": d["identity"]["model_id"], "artifact_sha256": d["identity"]["sha256"], "threshold": d["threshold"]["value"],
                  "threshold_source": d["threshold"]["source"], "clinical_fields_used": d["clinical_profile_fields_used"] or "none (sensor-only)",
                  "training_scope": d["training_scope"]})
