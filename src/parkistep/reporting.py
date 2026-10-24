"""JSON and Markdown report writers. All numbers come from the report dict built by the pipeline."""
from __future__ import annotations

import json
from pathlib import Path

from .config import Settings


def _pct(x):
    return "n/a" if x is None else f"{x:.2f}%"


def _n(x):
    return "n/a" if x is None else (f"{x:,}" if isinstance(x, int) else f"{x:,.4g}")


def write_reports(settings: Settings, report: dict, join_audit: dict | None) -> dict:
    rc = settings.project["reports"]
    out_dir = settings.reports_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    jpath, mpath, apath = out_dir / rc["json"], out_dir / rc["markdown"], out_dir / rc["join_audit"]
    jpath.write_text(json.dumps(report, indent=2, default=str))
    mpath.write_text(render_markdown(report), encoding="utf-8")
    paths = {"json": str(jpath), "markdown": str(mpath)}
    if join_audit is not None:
        apath.write_text(json.dumps(join_audit, indent=2, default=str))
        paths["join_audit"] = str(apath)
    return paths


def render_markdown(r: dict) -> str:
    L: list[str] = []
    ds = r["dataset"]
    L.append(f"# Data Quality Report - {ds['name']}")
    L.append("")
    if r.get("synthetic_test_data"):
        L.append("> **SYNTHETIC TEST DATA - this is not a real-data audit.** Statistics below describe a test fixture only.")
        L.append("")
    L.append(f"**Status: `{r['status']}`**  ")
    L.append(f"{r['status_reason']}")
    L.append("")
    L.append(f"Generated (UTC): {r['generated_at_utc']}  ")
    L.append(f"Real-data audit: `{r.get('real_data_audit')}`")
    L.append("")
    if r.get("real_data_audit") == "not_run":
        L.append("## Real-data audit not run")
        L.append("")
        L.append(f"Missing files (kind -> pattern searched in `{r['raw_dir']}`):")
        for k, v in (r.get("missing_files") or {}).items():
            L.append(f"- {k}: `{v}`")
        L.append("")
        L.append("Acquire the dataset (see `data/README.md`) and re-run the audit. No statistics are reported.")
        L.extend(_issues_md(r))
        return "\n".join(L) + "\n"

    L.append("## 1. Dataset overview")
    L.append("")
    L.append(f"- Version: {ds['version']}")
    L.append(f"- Source: {ds['source'].get('url')}")
    L.append(f"- License: {ds['license'].get('name')} ({ds['license'].get('url')})")
    L.append(f"- Attribution: {ds['attribution']}")
    L.append("")
    L.append("| File | Size (bytes) | Rows | Columns | SHA-256 |")
    L.append("|---|---:|---:|---:|---|")
    for k, d in (r.get("file_details") or {}).items():
        L.append(f"| {k}: `{Path(d['path']).name}` | {d['bytes']:,} | {d['rows']:,} | {d['columns']} | `{d['sha256'][:16]}...` |")
    L.append("")
    ov = r.get("overview", {})
    L.append("| Quantity | Value |")
    L.append("|---|---:|")
    for k, v in ov.items():
        L.append(f"| {k} | {_n(v)} |")
    L.append("")
    if ds.get("known_documentation_discrepancies"):
        L.append("Documentation discrepancies noted in the profile (verified against the files):")
        for d in ds["known_documentation_discrepancies"]:
            L.append(f"- {d}")
        L.append("")

    c = r.get("clinical")
    L.append("## 2. Clinical-data quality")
    L.append("")
    if c:
        L.append(f"Records: {c['n_records']}; distinct participants: {c['n_distinct_participants']}; rows sharing a duplicated key: {c['duplicate_key_rows']}.")
        L.append("")
        L.append("| Field | dtype | Missing | Missing % | Mean | Std | Min | Median | Max |")
        L.append("|---|---|---:|---:|---:|---:|---:|---:|---:|")
        for name, f in c["fields"].items():
            d = f.get("describe", {})
            L.append(f"| {name} | {f['dtype']} | {f['missing']} | {f['missing_pct']:.1f} | {_n(d.get('mean'))} | {_n(d.get('std'))} | "
                     f"{_n(d.get('min'))} | {_n(d.get('50%'))} | {_n(d.get('max'))} |")
        for name, f in c["fields"].items():
            if "value_counts" in f:
                L.append("")
                L.append(f"Categorical `{name}`: " + ", ".join(f"{k}={v}" for k, v in f["value_counts"].items()))
    else:
        L.append("Clinical statistics unavailable (schema validation failed).")
    L.append("")

    s = r.get("sensor")
    L.append("## 3. Sensor-data quality")
    L.append("")
    if s:
        L.append(f"Rows: {s['n_rows']:,}; participants: {s['n_participants']}; recordings (unique group_by combinations): {s['n_recordings']}.")
        L.append("")
        m = s["missing"]
        L.append(f"Missing sensor cells: {m['total_missing_cells']:,}; rows with any missing channel: {m['rows_with_any_missing']:,} ({_pct(m['rows_with_any_missing_pct'])}).")
        L.append("")
        L.append("| Sensor position | Rows with all channels missing | Rows partially missing |")
        L.append("|---|---:|---:|")
        for p, v in m["per_position"].items():
            L.append(f"| {p} | {v['rows_all_channels_missing']:,} | {v['rows_partially_missing']:,} |")
        L.append("")
        L.append(f"Channels: {len(s['channel_inventory'])} ({', '.join(x['name'] for x in s['channel_inventory'][:6])}, ...). "
                 f"Constant channels: {s['constant_channels'] or 'none'}; channels with out-of-range values: {len(s['implausible_values'])}.")
        sp = s["sampling"]
        L.append("")
        L.append("### Timestamps and sampling")
        L.append(f"- Duplicate timestamps (within recording): {sp.get('duplicate_timestamps')}")
        L.append(f"- Out-of-order steps: {sp.get('out_of_order_steps')}; non-contiguous recordings: {sp.get('recordings_non_contiguous')}")
        if "interval_s" in sp:
            i = sp["interval_s"]
            L.append(f"- Interval (s): median {i['median']:.6f}, min {i['min']:.6f}, max {i['max']:.6f}, std {i['std']:.3g}")
            L.append(f"- Estimated sampling frequency: {sp['estimated_hz_overall']:.4f} Hz overall "
                     f"(per recording {sp['estimated_hz_per_recording']['min']:.4f}-{sp['estimated_hz_per_recording']['max']:.4f} Hz); "
                     f"configured expectation: {sp.get('expected_hz')} Hz")
            L.append(f"- Fraction of intervals within tolerance of reference: {sp['fraction_within_tolerance']:.4f}")
            L.append(f"- Gaps above {sp['gap_threshold_s']:.4f}s: {sp['gaps']['count']}")
            for g in sp["gaps"].get("largest", []):
                L.append(f"  - {g['recording_id']}: {g['gap_s']:.3f}s (row {g['row']})")
        cov = s["coverage"]
        L.append(f"- Total recorded duration: {cov['total_duration_s']:.1f}s across {cov['n_recordings']} recordings")
    L.append("")

    a = r.get("annotation")
    L.append("## 4. Annotation quality")
    L.append("")
    if a:
        for name, f in a["fields"].items():
            lab = f["labels"]
            L.append(f"**{name}** ({f['role']}): " + ", ".join(
                f"{k}{' (' + lab[k] + ')' if k in lab else ''}: {v:,} ({f['value_pct'][k]:.2f}%)" for k, v in f["value_counts"].items())
                + f"; missing {f['missing']}; undocumented values {f['unexpected_values'] or 'none'}")
            L.append("")
        e = a.get("episodes")
        if e:
            L.append(f"Episodes: **{e['count']}** ({e['definition']}); participants with episodes: {e['participants_with_episodes']}.")
            if e["duration_s"]:
                d = e["duration_s"]
                L.append(f"Episode duration (s): mean {d['mean']:.2f}, median {d['50%']:.2f}, min {d['min']:.2f}, max {d['max']:.2f}.")
            L.append("")
            L.append("Episodes per participant: " + ", ".join(f"{k}: {v}" for k, v in e["per_participant"].items()))
            L.append("")
        if "severity_consistency" in a:
            L.append(f"Severity/event consistency: {a['severity_consistency']}")
        if "annotation_alignment" in a:
            al = a["annotation_alignment"]
            L.append(f"Annotation-frame alignment (informational): {al['runs_whose_length_is_multiple_of_frame']} of {al['label_runs']} "
                     f"label runs are multiples of {al['samples_per_annotation_frame']} samples ({al['fraction_multiple_of_frame']:.3f}).")
    L.append("")

    j = r.get("join")
    L.append("## 5. Join integrity")
    L.append("")
    if j and not j.get("skipped"):
        L.append(f"- Key: `{j['join_key']}`; type: {j['join_type']}; expected cardinality: {j['expected_cardinality']}; observed: {j['observed_cardinality']}")
        L.append(f"- Distinct participants: sensor {j['distinct_participants_sensor']}, clinical {j['distinct_participants_clinical']}, matched {j['matched_participants']}")
        L.append(f"- Sensor participants without clinical record: {j['sensor_participants_without_clinical'] or 'none'}")
        L.append(f"- Clinical participants without sensor data: {j['clinical_participants_without_sensor'] or 'none'}")
        L.append(f"- Sensor rows before/after: {j['sensor_rows_before']:,} / {_n(j.get('sensor_rows_after'))}")
        L.append(f"- Join validation passed: **{j['validation_passed']}**")
    elif j:
        L.append(f"Join not attempted: {j.get('reason')}")
    L.append("")
    L.append("## 6. Data readiness summary")
    L.append("")
    L.append(f"**{r['status']}** - {r['status_reason']}")
    L.append("")
    for chk in r.get("readiness", []):
        L.append(f"- {'PASS' if chk['passed'] else 'FAIL'}: {chk['check']} (required >= {chk['required']}, observed {chk['observed']})")
    L.append("")
    L.append("Phase 1 only validates source data and annotations; it does not establish a forecasting horizon or any predictive performance.")
    L.extend(_issues_md(r))
    return "\n".join(L) + "\n"


def _issues_md(r: dict) -> list[str]:
    L = ["", "## Issues", ""]
    issues = r.get("issues", [])
    if not issues:
        return L + ["None."]
    for sev in ("error", "warning", "info"):
        group = [i for i in issues if i["severity"] == sev]
        if group:
            L.append(f"### {sev.capitalize()}s ({len(group)})")
            for i in group:
                loc = f" _(at {i['location']})_" if i.get("location") else ""
                act = f" Action: {i['action']}" if i.get("action") else ""
                L.append(f"- **{i['code']}**: {i['message']}{loc}{act}")
            L.append("")
    return L
