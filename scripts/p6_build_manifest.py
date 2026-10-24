#!/usr/bin/env python
"""Phase 6 - build docs/phase6/FINAL_RELEASE_MANIFEST.json from the repository and the generated verification reports.

Nothing is typed in by hand: checksums are computed now, versions are observed now, results are read from the Phase 2 and Phase 6
report files. No patient-level data is written. There is no VCS metadata in the delivered archive, so the source revision is a
SHA-256 over the source tree (src, app, scripts, tests, configs, requirements, docs/phase6/architecture_src); a git commit is
added only when a .git directory exists.

    python scripts/p6_build_manifest.py [--release-id RELEASE_ID] [--out docs/phase6/FINAL_RELEASE_MANIFEST.json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SKIP = {"__pycache__", ".git", ".venv", ".pytest_cache"}


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def tree_hash(dirs: list[str], files: list[str]) -> tuple[str, int]:
    h, n = hashlib.sha256(), 0
    paths = [ROOT / f for f in files if (ROOT / f).is_file()]
    for d in dirs:
        paths += [p for p in sorted((ROOT / d).rglob("*")) if p.is_file() and not (set(p.relative_to(ROOT).parts) & SKIP) and p.suffix != ".pyc"]
    for p in sorted(set(paths)):
        h.update(rel(p).encode()); h.update(b"\0"); h.update(sha(p).encode()); h.update(b"\0"); n += 1
    return h.hexdigest(), n


def J(p):
    return json.loads((ROOT / p).read_text())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--release-id", default=None, help="default: <pyproject version>-phase6 (+ short source hash)")
    ap.add_argument("--out", default="docs/phase6/FINAL_RELEASE_MANIFEST.json")
    ap.add_argument("--junit", default="reports/phase6/junit_full_regression_final.xml")
    a = ap.parse_args()

    pyproj = (ROOT / "pyproject.toml").read_text()
    version = re.search(r'^version\s*=\s*"([^"]+)"', pyproj, re.M).group(1)
    src_hash, n_src = tree_hash(["src", "app", "scripts", "tests", "configs", "docs/phase6/architecture_src", "docs/phase6/presentation_src"],
                                ["requirements.txt", "requirements-lock.txt", "requirements-browser.txt", "pyproject.toml", "LICENSE", "THIRD_PARTY_NOTICES.md"])
    rid = a.release_id or f"{version}-phase6+{src_hash[:10]}"
    git = None
    if (ROOT / ".git").exists():
        try:
            git = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        except Exception:                                                # noqa: BLE001
            git = None

    # dataset provenance (file checksums computed now; no data content is copied)
    raw = ROOT / "data/raw/fogstar"
    prov = json.loads((raw / "PROVENANCE.json").read_text()) if (raw / "PROVENANCE.json").exists() else {}
    dataset = {"name": prov.get("dataset"), "version_declared": prov.get("version_declared"), "source_url": prov.get("source_url"), "license": prov.get("license"),
               "attribution": prov.get("attribution"), "acquired_at_utc": prov.get("acquired_at_utc"), "redistributed_in_repository": False,
               "files": {f: {"sha256": sha(raw / f), "bytes": (raw / f).stat().st_size} for f in ("sensor_data.csv", "clinical_data.csv") if (raw / f).exists()}}

    art = J("reports/phase6/artifact_verification.json")
    models = [{"manifest": rel(Path(x["manifest"])) if Path(x["manifest"]).is_absolute() else x["manifest"], "model_id": f"{x['identity']['experiment_id']}/{x['identity']['experiment']}/{x['identity']['model']}",
               "horizon_s": x["identity"]["horizon_s"], "uses_clinical": x["identity"]["uses_clinical"], "artifact_bytes": x.get("artifact_bytes"),
               "sha256": x.get("sha256_recomputed"), "sha256_matches_manifest": x["checks"].get("sha256_matches_manifest"), "threshold": x["threshold"]["value"],
               "threshold_policy": x["threshold"]["policy"], "verified": x["passed"]} for x in art["artifacts"]]
    p3 = yaml.safe_load((ROOT / "configs/phase3.yaml").read_text())["phase3"]["model"]
    default_model = {k: p3[k] for k in ("experiment", "model", "horizon_s")}

    configs = {rel(p): sha(p) for p in sorted((ROOT / "configs").glob("*.yaml"))}
    pk = ["pandas", "numpy", "PyYAML", "requests", "matplotlib", "pyarrow", "scikit-learn", "scipy", "joblib", "streamlit", "plotly", "pytest", "playwright", "psutil"]
    soft = {"python": platform.python_version(), "platform": platform.platform()}
    for k in pk:
        try: soft[k] = metadata.version(k)
        except metadata.PackageNotFoundError: soft[k] = None

    def ref(p):
        f = ROOT / p
        return {"path": p, "sha256": sha(f), "bytes": f.stat().st_size} if f.exists() else {"path": p, "missing": True}

    ju = ET.parse(ROOT / a.junit).getroot(); ju = ju if ju.tag == "testsuite" else ju[0]
    tests = {"junit": ref(a.junit), "tests": int(ju.attrib["tests"]), "failures": int(ju.attrib["failures"]), "errors": int(ju.attrib["errors"]), "skipped": int(ju.attrib["skipped"]),
             "seconds": float(ju.attrib["time"]), "command": "PARKISTEP_REQUIRE_REAL=1 python -m pytest -q -p no:cacheprovider"}
    bf = J("reports/phase6/browser_failure_paths.json"); mp = J("reports/phase6/browser_multi_patient.json"); sk = J("reports/phase6/soak_report.json")
    rc = J("reports/phase6/retrospective_recompute.json"); v1 = J("reports/phase6/phase1_warning_recheck.json")
    vr = [J("reports/phase6/visual_review_after_fix.json"), J("reports/phase6/visual_review_after_fix_b.json")]
    verification = {
        "full_regression": tests,
        "artifact_verification": {**ref("reports/phase6/artifact_verification.json"), "n_manifests": art["n_manifests"], "n_passed": art["n_passed"], "software_mismatch_in_any_artifact_on_this_run": art["software_mismatch_in_any_artifact"]},
        "retrospective_independent_recompute": {**ref("reports/phase6/retrospective_recompute.json"), "n_metrics_compared": len(rc["comparison"]), "n_discrepancies": rc["n_discrepancies"], "recording": rc["recording_id"]},
        "browser_failure_paths": {**ref("reports/phase6/browser_failure_paths.json"), "scenarios": {s["scenario"]: s["passed"] for s in bf["scenarios"]}, "checks": sum(len(s["checks"]) for s in bf["scenarios"]), "all_passed": bf["all_passed"]},
        "browser_multi_patient": {**ref("reports/phase6/browser_multi_patient.json"), "patients": len({c["patient_id"] for c in mp["discovered"]["combinations"]}), "recordings": len(mp["discovered"]["combinations"]),
                                  "checks": len(mp["checks"]), "all_passed": mp["all_passed"]},
        "soak": {**ref("reports/phase6/soak_report.json"), "cycles": sk["cycles_run"], "duration_s": sk["duration_s"], "rss_slope_mb_per_cycle_after_warmup": sk["rss_slope_mb_per_cycle_after_warmup"], "all_passed": sk["all_passed"], "limitations": sk["limitations"]},
        "visual_review": {"files": [ref("reports/phase6/visual_review_after_fix.json"), ref("reports/phase6/visual_review_after_fix_b.json")], "page_viewport_checks": sum(len(v["results"]) for v in vr),
                          "passed": sum(1 for v in vr for r in v["results"] if r["passed"])},
        "phase1_warning_recheck": {**ref("reports/phase6/phase1_warning_recheck.json"), "matches_documented_expectation": v1["matches_documented_expectation_from_PHASE1_VERIFICATION_md"]},
        "repository_audit": ref("reports/phase6/repo_audit.json"),
    }

    # README numbers must equal the generated Phase 2 results (default dashboard model, 3 s)
    m = J("reports/phase2/phase2_metrics.json"); r3 = m["results"][f"{default_model['experiment']}|{default_model['model']}|h{default_model['horizon_s']:g}"]
    readme = (ROOT / "README.md").read_text()
    q = {"average_precision": f"{r3['window']['average_precision']:.3f}", "event_sensitivity": f"{r3['event']['event_sensitivity']:.2f}",
         "false_alerts_per_hour": f"{r3['event']['false_alerts_per_hour']:.1f}", "n_events_detected": f"{r3['event']['n_events_detected']}/{r3['event']['n_events_evaluable']}"}
    readme_check = {k: {"value_in_phase2_metrics": v, "found_in_README": v in readme} for k, v in q.items()}

    man = {
        "schema": "parkistep.release_manifest/1", "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "release": {"id": rid, "project_version_pyproject": version, "git_commit": git, "vcs_note": None if git else "no .git directory in the delivered archive",
                    "source_tree_sha256": src_hash, "source_files_hashed": n_src, "status": "release candidate - see PHASE6_COMPLETION_REPORT.md for open items"},
        "dataset": dataset, "default_model": default_model, "models": models, "n_models": len(models), "experiment_ids": art["experiment_ids"],
        "configuration_snapshot_sha256": configs, "software_versions_observed": soft,
        "requirements_files": {f: ref(f) for f in ("requirements.txt", "requirements-lock.txt", "requirements-browser.txt")},
        "verification": verification,
        "evaluation_reports": {"phase2_metrics": ref("reports/phase2/phase2_metrics.json"), "phase2_results_report": ref("reports/phase2/phase2_results_report.md"), "phase1_quality_report": ref("reports/data_quality_report.json")},
        "architecture": {"pdf": ref("docs/phase6/architecture.pdf"), "svg": ref("docs/phase6/architecture.svg"), "source": ref("docs/phase6/architecture_src/architecture.dot"), "slide_figure": ref("docs/phase6/architecture_src/architecture_slide.png")},
        "presentation": {"pptx": ref("docs/phase6/ParkiStep_Twin_Presentation.pptx"), "pdf": ref("docs/phase6/ParkiStep_Twin_Presentation.pdf"), "builder": ref("docs/phase6/presentation_src/build_presentation.py")},
        "licence": {"code": ref("LICENSE"), "third_party": ref("THIRD_PARTY_NOTICES.md"), "copyright_holder": "PENDING - not supplied"},
        "readme_numbers_vs_generated_results": readme_check, "readme_numbers_all_match": all(v["found_in_README"] for v in readme_check.values()),
        "demo_video": {"script": ref("docs/phase6/DEMO_VIDEO_SCRIPT.md"), "video_file": None, "status": "NOT RECORDED - only a script exists"},
        "team": {"name": "PENDING", "members": "PENDING", "college": "PENDING", "repository_url": "PENDING", "video_url": "PENDING"},
        "contains_patient_level_data": False,
    }
    out = ROOT / a.out; out.parent.mkdir(parents=True, exist_ok=True); out.write_text(json.dumps(man, indent=2, default=str))
    print("wrote", out, "| release", rid, "| models", len(models), "| README numbers match:", man["readme_numbers_all_match"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
