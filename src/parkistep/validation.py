"""Configuration-driven schema mapping, type conversion and field-level validation.

Original column names appear only in the dataset profile; this module maps them to
canonical names and never guesses: missing or ambiguous columns become issues.
"""
from __future__ import annotations

import difflib

import numpy as np
import pandas as pd

from .config import channel_specs
from .issues import Issues, severity_from_config

_UNIT_SCALE = {"seconds": 1.0, "milliseconds": 1e-3, "microseconds": 1e-6}  # generic unit conversion


def resolve_columns(available: list[str], fields: dict, location: str, issues: Issues) -> dict[str, str]:
    """Map canonical name -> source column using configured aliases."""
    mapping: dict[str, str] = {}
    for canon, spec in fields.items():
        aliases = spec["source"]
        present = [a for a in aliases if a in available]
        if len(present) == 1:
            mapping[canon] = present[0]
        elif len(present) > 1:
            issues.add("error", "COLUMN_AMBIGUOUS",
                       f"Several source columns match canonical field '{canon}': {present}.",
                       location=f"{location}.{canon}.source", action="Keep only one alias in the profile or fix the file.")
        else:
            required = spec.get("required", True)
            close = difflib.get_close_matches(aliases[0], available, n=3, cutoff=0.5)
            hint = f" Closest columns in the file: {close}." if close else ""
            issues.add("error" if required else "warning", "COLUMN_MISSING",
                       f"No source column found for canonical field '{canon}' (looked for {aliases}).{hint}",
                       location=f"{location}.{canon}.source",
                       action="Add the real column name to `source` in the profile (do not rename data silently).")
    return mapping


def convert_series(s: pd.Series, dtype: str, canon: str, location: str, issues: Issues,
                   unparseable_severity: str = "error") -> pd.Series:
    """Convert to the configured dtype; unparseable values are reported, never silently dropped."""
    if dtype == "str":
        out = s.astype("string").str.strip()
        return out
    numeric = pd.to_numeric(s, errors="coerce")
    bad = numeric.isna() & s.notna()
    if bad.any():
        issues.add(unparseable_severity, "VALUE_UNPARSEABLE",
                   f"{int(bad.sum())} value(s) in '{canon}' are not numeric (e.g. {s[bad].iloc[0]!r}); set to missing.",
                   location=location, action="Inspect the source file; correct the dtype or the data.",
                   count=int(bad.sum()))
    if dtype == "int":
        nn = numeric.dropna()
        if len(nn) and not np.all(np.mod(nn.to_numpy(), 1) == 0):
            issues.add("error", "VALUE_NOT_INTEGER", f"Field '{canon}' has non-integer values but dtype is int.",
                       location=location, action="Change dtype to float or fix the source values.")
            return numeric.astype("float64")
        return numeric.round().astype("Int64")
    return numeric.astype("float64")


def build_clinical(raw: pd.DataFrame, profile: dict, file_name: str) -> tuple[pd.DataFrame | None, dict, Issues]:
    """Map and type the clinical table. Returns canonical frame (or None on critical failure)."""
    issues = Issues()
    fields = profile["clinical"]["fields"]
    loc = "clinical.fields"
    mapping = resolve_columns(list(raw.columns), fields, loc, issues)
    required_missing = [c for c, sp in fields.items() if sp.get("required", True) and c not in mapping]
    info = {"column_mapping": mapping,
            "unmapped_source_columns": [c for c in raw.columns if c not in mapping.values()]}
    if info["unmapped_source_columns"]:
        issues.add("info", "COLUMNS_UNMAPPED", f"Clinical columns not mapped to any canonical field: {info['unmapped_source_columns']}.",
                   location=file_name)
    if required_missing:
        issues.add("error", "CLINICAL_SCHEMA_INVALID",
                   f"Clinical table cannot be processed: required field(s) missing: {required_missing}.",
                   location=file_name, action="Update the column mapping in the profile to match the real header.")
        return None, info, issues
    out = pd.DataFrame(index=raw.index)
    sev = severity_from_config(profile.get("validation", {}).get("sensor", {}).get("unparseable_value_severity"), "error")
    for canon, src in mapping.items():
        out[canon] = convert_series(raw[src], fields[canon].get("dtype", "str"), canon, f"{file_name}:{src}", issues, sev)
    out["source_row"] = np.arange(len(out), dtype="int64")
    return out, info, issues


def validate_clinical(df: pd.DataFrame, profile: dict) -> Issues:
    issues = Issues()
    vcfg = profile.get("validation", {}).get("clinical", {})
    key = profile["clinical"]["key"]
    fields = profile["clinical"]["fields"]
    if key not in df.columns:
        return issues
    if df[key].isna().any():
        issues.add("error", "CLINICAL_KEY_MISSING", f"{int(df[key].isna().sum())} clinical row(s) have no '{key}'.",
                   location=f"clinical.key={key}", action="Fix the source file; rows cannot be linked without an identifier.")
    dup = df[key].dropna().duplicated(keep=False)
    if profile["clinical"].get("one_record_per_key", True) and dup.any():
        ids = sorted(df.loc[df[key].notna()][dup.reindex(df.index, fill_value=False)][key].unique().tolist())
        issues.add(severity_from_config(vcfg.get("duplicate_key_severity"), "error"), "CLINICAL_DUPLICATE_KEY",
                   f"Duplicate clinical identifier(s) for '{key}': {ids}.", location=f"clinical.key={key}",
                   action="Resolve duplicates in the source or set one_record_per_key: false with a documented rule.",
                   ids=ids)
    miss_sev = severity_from_config(vcfg.get("missing_value_severity"), "warning")
    oor_sev = severity_from_config(vcfg.get("out_of_range_severity"), "warning")
    cat_sev = severity_from_config(vcfg.get("unexpected_category_severity"), "warning")
    for canon, spec in fields.items():
        if canon not in df.columns:
            continue
        col = df[canon]
        n_miss = int(col.isna().sum())
        if n_miss and canon != key:
            ids = df.loc[col.isna(), key].tolist() if key in df.columns else []
            issues.add(miss_sev, "CLINICAL_MISSING_VALUES",
                       f"'{canon}' is missing for {n_miss} participant(s) (ids: {ids}); values are retained as missing, not imputed.",
                       location=f"clinical.fields.{canon}", count=n_miss, ids=ids)
        vr = spec.get("valid_range")
        if vr is not None and pd.api.types.is_numeric_dtype(col):
            out = col.notna() & ((col < vr[0]) | (col > vr[1]))
            if out.any():
                ids = df.loc[out, key].tolist() if key in df.columns else []
                issues.add(oor_sev, "CLINICAL_OUT_OF_RANGE",
                           f"'{canon}' has {int(out.sum())} value(s) outside the documented range {vr} "
                           f"(participants {ids}; values {col[out].tolist()}). Flagged for review, not altered.",
                           location=f"clinical.fields.{canon}.valid_range", count=int(out.sum()), ids=ids)
        cats = spec.get("categories")
        if cats is not None:
            unexpected = sorted(set(col.dropna().astype(str)) - {str(c) for c in cats})
            if unexpected:
                issues.add(cat_sev, "CLINICAL_UNEXPECTED_CATEGORY",
                           f"'{canon}' has value(s) not in the configured categories {cats}: {unexpected}.",
                           location=f"clinical.fields.{canon}.categories", values=unexpected)
    return issues


def build_sensor(raw: pd.DataFrame, profile: dict, file_name: str) -> tuple[pd.DataFrame | None, dict, Issues]:
    """Map and type the sensor table into canonical columns, preserving original row order."""
    issues = Issues()
    s = profile["sensor"]
    sev = severity_from_config(profile.get("validation", {}).get("sensor", {}).get("unparseable_value_severity"), "error")
    cols = list(raw.columns)
    ident_map = resolve_columns(cols, s["identifiers"], "sensor.identifiers", issues)
    ts_map = resolve_columns(cols, {s["timestamp"]["canonical"]: {"source": s["timestamp"]["source"], "required": True}},
                             "sensor.timestamp", issues)
    chans = channel_specs(profile)
    ch_map = resolve_columns(cols, {c["canonical"]: {"source": [c["source"]], "required": True} for c in chans},
                             "sensor.channels", issues)
    ann_map = resolve_columns(cols, s.get("annotations", {}), "sensor.annotations", issues)
    mapping = {**ident_map, **ts_map, **ch_map, **ann_map}
    info = {"column_mapping": mapping,
            "unmapped_source_columns": [c for c in cols if c not in mapping.values()]}
    if info["unmapped_source_columns"]:
        issues.add("info", "COLUMNS_UNMAPPED", f"Sensor columns not mapped to any canonical field: {info['unmapped_source_columns']}.",
                   location=file_name)
    if issues.has_errors:
        issues.add("error", "SENSOR_SCHEMA_INVALID",
                   "Sensor table cannot be processed because required columns could not be mapped (see errors above).",
                   location=file_name, action="Update the mapping in the profile to match the real header.")
        return None, info, issues
    out = pd.DataFrame(index=raw.index)
    for canon, src in ident_map.items():
        out[canon] = convert_series(raw[src], s["identifiers"][canon].get("dtype", "int"), canon, f"{file_name}:{src}", issues, sev)
    ts = s["timestamp"]
    src = ts_map[ts["canonical"]]
    if ts.get("kind", "numeric") == "numeric":
        num = convert_series(raw[src], "float", ts["canonical"], f"{file_name}:{src}", issues, sev)
        out[ts["canonical"]] = num * _UNIT_SCALE[ts["unit"]]
    else:
        parsed = pd.to_datetime(raw[src], format=ts.get("format"), errors="coerce", utc=True)
        bad = parsed.isna() & raw[src].notna()
        if bad.any():
            issues.add(sev, "TIMESTAMP_UNPARSEABLE", f"{int(bad.sum())} timestamp value(s) could not be parsed.",
                       location=f"{file_name}:{src}", action="Check `sensor.timestamp.format`.")
        out[ts["canonical"]] = (parsed.astype("int64") / 1e9).where(parsed.notna())
    for c in chans:
        out[c["canonical"]] = convert_series(raw[ch_map[c["canonical"]]], "float", c["canonical"],
                                             f"{file_name}:{ch_map[c['canonical']]}", issues, sev)
    for canon, spec in s.get("annotations", {}).items():
        out[canon] = convert_series(raw[ann_map[canon]], spec.get("dtype", "int"), canon, f"{file_name}:{ann_map[canon]}", issues, sev)
    out["source_row"] = np.arange(len(out), dtype="int64")
    out.insert(0, "recording_id", _recording_ids(out, s["group_by"]))
    return out, info, issues


def _recording_ids(df: pd.DataFrame, group_by: list[str]) -> pd.Series:
    parts = [g[:1] + df[g].astype("string") for g in group_by]   # e.g. p12-s1-t3 style id from configured keys
    rid = parts[0]
    for p in parts[1:]:
        rid = rid + "-" + p
    return rid
