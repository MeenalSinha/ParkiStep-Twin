"""Participant-level join of the clinical and sensor streams, with a machine-readable audit."""
from __future__ import annotations

import pandas as pd

from .issues import Issues, severity_from_config


def join_streams(sensor: pd.DataFrame, clinical: pd.DataFrame, profile: dict) -> tuple[pd.DataFrame | None, dict, Issues]:
    """Join by the configured key only (never by position). Returns (joined or None, audit, issues)."""
    issues = Issues()
    jcfg = profile["join"]
    key, how, expected = jcfg["key"], jcfg["how"], jcfg["expected_cardinality"]
    sev_s = severity_from_config(jcfg.get("unmatched_sensor_participants_severity"), "error")
    sev_c = severity_from_config(jcfg.get("unmatched_clinical_participants_severity"), "warning")
    sev_m = severity_from_config(jcfg.get("missing_clinical_after_join_severity"), "warning")

    s_ids = set(sensor[key].dropna().unique().tolist())
    c_ids = set(clinical[key].dropna().unique().tolist())
    matched = s_ids & c_ids
    only_s, only_c = sorted(s_ids - c_ids), sorted(c_ids - s_ids)
    dup_clin = int(clinical[key].dropna().duplicated().sum())
    observed_card = "many_to_one" if dup_clin == 0 else "many_to_many"
    audit = {
        "join_key": key, "join_type": how, "expected_cardinality": expected,
        "observed_cardinality": observed_card,
        "distinct_participants_sensor": len(s_ids), "distinct_participants_clinical": len(c_ids),
        "matched_participants": len(matched),
        "sensor_participants_without_clinical": [int(x) if float(x).is_integer() else x for x in only_s],
        "clinical_participants_without_sensor": [int(x) if float(x).is_integer() else x for x in only_c],
        "sensor_null_keys": int(sensor[key].isna().sum()),
        "duplicate_clinical_keys": dup_clin,
        "sensor_rows_before": int(len(sensor)), "clinical_rows": int(len(clinical)),
        "validation_passed": False, "warnings": [], "failures": [],
    }
    if audit["sensor_null_keys"]:
        issues.add("error", "JOIN_NULL_KEYS", f"{audit['sensor_null_keys']} sensor row(s) have no '{key}'.", location=key)
    if only_s:
        issues.add(sev_s, "JOIN_SENSOR_UNMATCHED", f"Sensor participant(s) without a clinical record: {only_s}.",
                   location=f"join.key={key}", action="Check identifier formats/mapping; unmatched rows are never hidden.")
    if only_c:
        issues.add(sev_c, "JOIN_CLINICAL_UNMATCHED", f"Clinical participant(s) without sensor recordings: {only_c}.",
                   location=f"join.key={key}")
    if dup_clin:
        issues.add("error", "JOIN_DUPLICATE_CLINICAL_KEY",
                   f"{dup_clin} duplicate clinical key(s); the join would multiply sensor rows (many-to-many).",
                   location=f"join.key={key}", action="Resolve duplicates in the clinical table.")
    if issues.has_errors:
        audit["failures"] = [i.message for i in issues if i.severity == "error"]
        audit["warnings"] = [i.message for i in issues if i.severity == "warning"]
        return None, audit, issues

    clin = clinical.drop(columns=[c for c in ("source_row",) if c in clinical.columns])
    try:
        joined = sensor.merge(clin, on=key, how=how, validate="m:1", indicator="_merge_status")
    except pd.errors.MergeError as exc:
        issues.add("error", "JOIN_CARDINALITY_VIOLATION", f"Join violated many-to-one cardinality: {exc}", location=f"join.key={key}")
        audit["failures"] = [str(exc)]
        return None, audit, issues
    audit["sensor_rows_after"] = int(len(joined))
    audit["rows_without_clinical_match"] = int((joined["_merge_status"] == "left_only").sum())
    if len(joined) != len(sensor) and how == "left":
        issues.add("error", "JOIN_ROW_COUNT_CHANGED",
                   f"Sensor rows changed from {len(sensor):,} to {len(joined):,} after a left join.", location="join")
        audit["failures"].append("row count changed")
        return None, audit, issues
    clin_cols = [c for c in clin.columns if c != key]
    miss_after = {c: int(joined[c].isna().sum()) for c in clin_cols}
    audit["missing_clinical_values_after_join_rows"] = miss_after
    if any(miss_after.values()):
        issues.add(sev_m, "JOIN_MISSING_CLINICAL_AFTER",
                   "Some clinical attributes are missing for joined sensor rows (propagated from missing clinical values): "
                   + ", ".join(f"{k}={v:,}" for k, v in miss_after.items() if v), location="joined view")
    joined = joined.drop(columns=["_merge_status"])
    audit["warnings"] = [i.message for i in issues if i.severity == "warning"]
    audit["failures"] = [i.message for i in issues if i.severity == "error"]
    audit["validation_passed"] = not audit["failures"]
    return joined, audit, issues
