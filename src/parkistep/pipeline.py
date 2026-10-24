"""Orchestration: discover -> load -> map/validate -> quality -> join -> status -> outputs."""
from __future__ import annotations

import json
import logging
import platform
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import __version__, quality, validation
from .config import Settings
from .ingestion import get_adapter
from .integration import join_streams
from .issues import Issues, PipelineError

log = logging.getLogger(__name__)

PASS, PASS_W, FAIL, NOT_RUN = "PASS", "PASS_WITH_WARNINGS", "FAIL", "NOT_RUN"


@dataclass
class AuditResult:
    status: str
    status_reason: str
    report: dict
    issues: Issues
    clinical: pd.DataFrame | None = None
    sensor: pd.DataFrame | None = None
    joined: pd.DataFrame | None = None
    join_audit: dict | None = None
    raw_tables: dict = field(default_factory=dict)


def _versions() -> dict:
    out = {"python": sys.version.split()[0], "platform": platform.platform(), "parkistep": __version__,
           "pandas": pd.__version__, "numpy": np.__version__}
    for mod in ("pyarrow", "matplotlib", "yaml"):
        try:
            out[mod] = __import__(mod).__version__
        except Exception:
            out[mod] = None
    return out


def _base_report(settings: Settings) -> dict:
    ds = settings.dataset
    return {
        "report_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "synthetic_test_data": bool(ds.get("synthetic", False)),
        "dataset": {"key": settings.dataset_key, "name": ds["name"], "version": ds["version"],
                    "source": ds.get("source", {}), "license": ds.get("license", {}),
                    "attribution": ds.get("attribution"), "citations": ds.get("citations", []),
                    "known_documentation_discrepancies": ds.get("known_documentation_discrepancies", [])},
        "software": _versions(),
        "raw_dir": str(settings.raw_dir),
        "missing_data_policy": ds.get("missing_data_policy", "retain"),
    }


def _finalize(settings, report, issues, joined_ok) -> tuple[str, str]:
    if report.get("real_data_audit") == "not_run":
        return NOT_RUN, "Dataset files were not found; the real-data audit was not run and no statistics are reported."
    if issues.has_errors:
        codes = sorted({i.code for i in issues.errors})
        return FAIL, f"{len(issues.errors)} critical error(s): {', '.join(codes)}."
    if issues.warnings:
        codes = sorted({i.code for i in issues.warnings})
        return PASS_W, (f"All critical checks passed (required columns, timestamp integrity, join validity, annotation "
                        f"availability, forecasting prerequisites); {len(issues.warnings)} warning(s) remain: {', '.join(codes)}.")
    return PASS, "All critical configured validation checks passed with no warnings."


def run_audit(settings: Settings, keep_frames: bool = True) -> AuditResult:
    report = _base_report(settings)
    issues = Issues()
    adapter = get_adapter(settings)
    disc = adapter.discover()
    report["files"] = {k: str(v) for k, v in disc.found.items()}
    if not disc.complete:
        issues.extend(disc.issues)
        report["real_data_audit"] = "not_run"
        report["missing_files"] = disc.missing
        status, reason = _finalize(settings, report, issues, False)
        report.update(status=status, status_reason=reason, issues=issues.to_dicts())
        return AuditResult(status, reason, report, issues)

    report["real_data_audit"] = "synthetic_fixture" if settings.dataset.get("synthetic") else "run"
    raws = {}
    for kind, path in disc.found.items():
        tbl, iss = adapter.load(kind, path)
        issues.extend(iss)
        if tbl is not None:
            raws[kind] = tbl
    report["file_details"] = {k: {"path": str(t.path), "bytes": t.size_bytes, "sha256": t.sha256,
                                  "rows": int(len(t.df)), "columns": int(t.df.shape[1])} for k, t in raws.items()}
    if len(raws) < 2:
        status, reason = _finalize(settings, report, issues, False)
        report.update(status=status, status_reason=reason, issues=issues.to_dicts())
        return AuditResult(status, reason, report, issues)

    prof = settings.dataset
    clin, cinfo, ciss = validation.build_clinical(raws["clinical"].df, prof, raws["clinical"].path.name)
    sens, sinfo, siss = validation.build_sensor(raws["sensor"].df, prof, raws["sensor"].path.name)
    issues.extend(ciss)
    issues.extend(siss)
    report["schema_mapping"] = {"clinical": cinfo, "sensor": {**sinfo, "column_mapping": sinfo["column_mapping"]},
                                "timestamp_unit": prof["sensor"]["timestamp"]["unit"],
                                "timestamp_canonical": prof["sensor"]["timestamp"]["canonical"]}
    join_audit = None
    joined = None
    ann = {}
    if clin is not None:
        issues.extend(validation.validate_clinical(clin, prof))
        report["clinical"] = quality.clinical_stats(clin, prof)
    if sens is not None:
        report["sensor"] = quality.sensor_stats(sens, prof, settings.project.get("reports", {}), issues)
        ann = quality.annotation_stats(sens, prof, issues)
        report["annotation"] = ann
        report["readiness"] = quality.readiness_checks(prof, ann, issues)
    if clin is not None and sens is not None and not issues.has_errors:
        joined, join_audit, jiss = join_streams(sens, clin, prof)
        issues.extend(jiss)
    else:
        join_audit = {"validation_passed": False, "skipped": True,
                      "reason": "Join not attempted because earlier critical errors were found "
                                "(clinical or sensor table could not be validated)."}
    report["join"] = join_audit
    ov = {"n_participants_sensor": report.get("sensor", {}).get("n_participants"),
          "n_participants_clinical": report.get("clinical", {}).get("n_distinct_participants"),
          "n_sensor_rows": report.get("sensor", {}).get("n_rows"),
          "n_clinical_records": report.get("clinical", {}).get("n_records"),
          "n_recordings": report.get("sensor", {}).get("n_recordings"),
          "n_annotated_episodes": (ann.get("episodes") or {}).get("count")}
    for g in prof["sensor"]["group_by"]:
        ov[f"n_distinct_{g}"] = report.get("sensor", {}).get(f"n_distinct_{g}")
    report["overview"] = ov
    status, reason = _finalize(settings, report, issues, joined is not None)
    report.update(status=status, status_reason=reason, issues=issues.to_dicts())
    log.info("Audit status: %s - %s", status, reason)
    return AuditResult(status, reason, report, issues,
                       clin if keep_frames else None, sens if keep_frames else None,
                       joined if keep_frames else None, join_audit, raws)


# ---------------------------------------------------------------- outputs
def write_table(df: pd.DataFrame, base: Path, fmt: str, fallback: str | None, compression: str | None) -> Path:
    base.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "parquet":
        try:
            import pyarrow  # noqa: F401
            path = base.with_suffix(".parquet")
            df.to_parquet(path, index=False, compression=compression)
            return path
        except ImportError:
            if not fallback:
                raise PipelineError("Parquet output needs the optional dependency 'pyarrow' (pip install pyarrow), "
                                    "or set outputs.format: csv / outputs.fallback_format: csv.")
            log.warning("pyarrow not installed; falling back to %s output", fallback)
            fmt = fallback
    if fmt == "csv":
        path = base.with_suffix(".csv")
        df.to_csv(path, index=False)
        return path
    raise PipelineError(f"Unsupported output format {fmt!r}; use parquet or csv.")


def prepare_outputs(settings: Settings, result: AuditResult) -> dict:
    """Write validated tables + manifest. Refuses when the audit has critical errors or did not run."""
    if result.status in (FAIL, NOT_RUN) or result.clinical is None or result.sensor is None:
        raise PipelineError(
            f"Cannot prepare processed outputs: audit status is {result.status}. {result.status_reason}",
            list(result.issues.errors))
    oc = settings.project["outputs"]
    out_dir = settings.processed_dir
    stems = oc["file_stems"]
    fmt, fb, comp = oc["format"], oc.get("fallback_format"), oc.get("compression")
    files = {"clinical": write_table(result.clinical, out_dir / stems["clinical"], fmt, fb, comp),
             "sensor": write_table(result.sensor, out_dir / stems["sensor"], fmt, fb, comp)}
    if oc.get("write_joined", True) and result.joined is not None:
        files["joined"] = write_table(result.joined, out_dir / stems["joined"], fmt, fb, comp)
    prof = settings.dataset
    manifest = {
        "dataset": result.report["dataset"], "synthetic_test_data": result.report["synthetic_test_data"],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "audit_status": result.status,
        "raw_files": result.report.get("file_details"),
        "processed_files": {k: {"path": str(v.relative_to(settings.root)) if v.is_relative_to(settings.root) else str(v),
                                "rows": int({"clinical": len(result.clinical), "sensor": len(result.sensor),
                                             "joined": len(result.joined) if result.joined is not None else 0}[k]),
                                "bytes": v.stat().st_size} for k, v in files.items()},
        "schema_mapping": result.report.get("schema_mapping"),
        "timestamp_interpretation": {"canonical_column": prof["sensor"]["timestamp"]["canonical"],
                                     "source_unit": prof["sensor"]["timestamp"]["unit"], "canonical_unit": "seconds",
                                     "timestamps_modified": False},
        "observed_sampling": {k: v for k, v in (result.report.get("sensor", {}).get("sampling", {})).items()
                              if k in ("estimated_hz_overall", "estimated_hz_per_recording", "interval_s", "gaps",
                                       "expected_hz", "duplicate_timestamps", "out_of_order_steps")},
        "sensor_channels": result.report.get("sensor", {}).get("channel_inventory"),
        "clinical_features": [c for c in result.clinical.columns if c not in ("source_row",)],
        "missing_data_handling": {"policy": prof.get("missing_data_policy", "retain"),
                                  "description": "Missing values are retained as missing. No imputation, interpolation, clipping or row deletion was applied."},
        "transformations": [
            "Original column names mapped to canonical names via the dataset profile",
            "Values converted to configured dtypes (unparseable values would be reported, not dropped)",
            "Timestamps converted to seconds using the configured unit (no resampling, no re-timing)",
            "recording_id and source_row columns added for traceability; source row order preserved",
            "Clinical attributes joined to sensor rows by the configured key (left join, many-to-one)"],
        "join_audit": result.join_audit,
        "configuration_snapshot": {"dataset_profile": prof, "outputs": oc},
        "software": result.report["software"],
    }
    mpath = out_dir / oc["manifest"]
    mpath.write_text(json.dumps(manifest, indent=2, default=str))
    files["manifest"] = mpath
    log.info("Wrote processed outputs to %s", out_dir)
    return {k: str(v) for k, v in files.items()}
