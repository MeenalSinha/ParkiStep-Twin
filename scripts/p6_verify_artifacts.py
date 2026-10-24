#!/usr/bin/env python
"""Phase 6 - verify every model artifact discovered from the manifests (nothing is assumed about count or names).

For each `*.manifest.json` under --artifacts-root: file exists, SHA-256 recomputed and compared, required manifest keys,
experiment id matches its directory, horizon matches the artifact name, threshold finite (value + provenance reported),
non-empty feature schema, then the artifact is loaded through the genuine ForecastPredictor and ONE real window from a real
recording (default: the first recording in the processed sensor table; configurable) is scored. Writes a JSON report.

    python scripts/p6_verify_artifacts.py [--artifacts-root artifacts/phase2] [--out reports/phase6/artifact_verification.json]
Exit code 0 only if every artifact passes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from parkistep.forecast.inference import ForecastPredictor  # noqa: E402
from parkistep.twin.config import load_phase3  # noqa: E402
from parkistep.twin.model import REQUIRED_MANIFEST_KEYS  # noqa: E402
from parkistep.twin.streams import Phase1ClinicalSource, load_processed_tables  # noqa: E402


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/phase3.yaml")
    ap.add_argument("--artifacts-root", default=None, help="default: parent of the Phase 3 model directory")
    ap.add_argument("--recording", default=None, help="recording id to take the real window from (default: first in the table)")
    ap.add_argument("--window-offset-s", type=float, default=30.0, help="how far into the recording the real window ends")
    ap.add_argument("--out", default="reports/phase6/artifact_verification.json")
    a = ap.parse_args()

    s3 = load_phase3(a.config, Path("."), [])
    root = Path(a.artifacts_root) if a.artifacts_root else s3.model_dir.parent.parent
    manifests = sorted(root.rglob("*.manifest.json"))
    sensor, _ = load_processed_tables(s3)
    cols = {"rec": "recording_id", "pid": "participant_id", "ts": s3.profile["sensor"]["timestamp"]["canonical"]}
    rec = a.recording or str(sensor[cols["rec"]].iloc[0])
    srec = sensor[sensor[cols["rec"]].astype(str) == rec].reset_index(drop=True)
    pid = str(srec[cols["pid"]].iloc[0])
    clin_src = Phase1ClinicalSource(s3)
    clinical = clin_src.get(pid)

    rows, ok_all = [], True
    for mp in manifests:
        r = {"manifest": str(mp), "checks": {}, "problems": []}
        try:
            m = json.loads(mp.read_text())
            art = mp.parent / str(m.get("artifact", ""))
            ck = r["checks"]
            ck["manifest_has_required_keys"] = not [k for k in REQUIRED_MANIFEST_KEYS if k not in m] or not (m.get("uses_clinical") is False and False)
            miss = [k for k in REQUIRED_MANIFEST_KEYS if k not in m]
            # clinical_source_fields is only required by fusion models; the gateway enforces that separately
            ck["manifest_has_required_keys"] = not miss
            if miss: r["problems"].append(f"missing keys {miss}")
            ck["artifact_exists"] = art.is_file()
            if art.is_file():
                got = sha256_file(art)
                ck["sha256_algorithm_is_sha256_hex64"] = isinstance(m.get("sha256"), str) and len(m["sha256"]) == 64
                ck["sha256_matches_manifest"] = got == m.get("sha256")
                r["sha256_recomputed"], r["sha256_manifest"], r["artifact_bytes"] = got, m.get("sha256"), art.stat().st_size
                if got != m.get("sha256"): r["problems"].append("checksum mismatch")
            else:
                r["problems"].append(f"artifact file missing: {art}")
            ck["experiment_id_matches_directory"] = m.get("experiment_id") in mp.parts
            stem = mp.name[: -len(".manifest.json")]
            parts = stem.split("__")
            hz = float(parts[-1][1:]) if parts[-1].startswith("h") else None
            ck["name_matches_manifest_identity"] = (parts[0], parts[1]) == (m.get("experiment"), m.get("model")) and hz is not None and abs(hz - float(m["horizon_s"])) < 1e-9
            ck["artifact_name_matches_stem"] = m.get("artifact") == f"{stem}.joblib"
            t = (m.get("threshold") or {})
            tv = t.get("threshold")
            ck["threshold_finite"] = isinstance(tv, (int, float)) and math.isfinite(tv)
            r["threshold"] = {"value": tv, "policy": t.get("policy"), "inner_false_alerts_per_hour": t.get("inner_false_alerts_per_hour"),
                              "inner_event_sensitivity": t.get("inner_event_sensitivity"), "above_one_alert_disabled": bool(ck["threshold_finite"] and tv > 1.0)}
            ck["feature_schema_nonempty"] = bool((m.get("feature_manifest") or {}).get("sensor_features"))
            ck["training_scope_recorded"] = bool((m.get("training") or {}).get("fitted_on"))
            r["identity"] = {"experiment_id": m.get("experiment_id"), "experiment": m.get("experiment"), "model": m.get("model"),
                             "horizon_s": m.get("horizon_s"), "uses_clinical": m.get("uses_clinical"), "n_sensor_features": len((m.get("feature_manifest") or {}).get("sensor_features", [])),
                             "window": m.get("window"), "software": m.get("software")}
            # software versions recorded at training time vs the running environment (a mismatch is surfaced, never hidden;
            # it does not fail the checksum gate because the artifact bytes are intact)
            from importlib import metadata as _md
            mism = {}
            for pkg, key in (("scikit-learn", "scikit-learn"), ("numpy", "numpy"), ("pandas", "pandas"), ("scipy", "scipy"), ("joblib", "joblib")):
                want = (m.get("software") or {}).get(key)
                try: have = _md.version(pkg)
                except Exception: have = None                                  # noqa: BLE001
                if want and have and want != have: mism[pkg] = {"trained_with": want, "running": have}
            r["software_mismatch"] = mism
            if ck.get("sha256_matches_manifest"):
                pred = ForecastPredictor.load(mp.parent, stem)           # re-verifies SHA-256 and loads with joblib
                W = m["window"]; dt = float(m["training"]["median_dt_s"]); n = int(round(W["input_window_s"] / dt)) + 1
                t0 = float(srec[cols["ts"]].iloc[0]); end_idx = int(np.searchsorted(srec[cols["ts"]].to_numpy(), t0 + a.window_offset_s))
                end_idx = max(end_idx, n)
                win = srec.iloc[end_idx - n + 1 : end_idx + 1] if end_idx - n + 1 >= 0 else srec.iloc[:n]
                win = win.iloc[-n:]
                res = pred.predict(win, cols["ts"], clinical=clinical if m["uses_clinical"] else None)
                valid = res.probability is None or (math.isfinite(res.probability) and 0.0 <= res.probability <= 1.0)
                ck["real_inference_ran"] = True
                ck["prediction_in_unit_interval_or_unavailable"] = valid
                ck["prediction_horizon_matches_manifest"] = abs(res.horizon_s - float(m["horizon_s"])) < 1e-9
                ck["prediction_threshold_matches_manifest"] = res.threshold is None or abs(res.threshold - float(tv)) < 1e-12
                ck["prediction_model_id_matches"] = res.model_id == f"{m['experiment_id']}/{m['experiment']}/{m['model']}"
                ck["clinical_flag_consistent"] = res.used_clinical == bool(m["uses_clinical"])
                r["inference"] = {"recording": rec, "window_end_t": res.prediction_time, "probability": res.probability, "data_quality": res.data_quality,
                                  "above_threshold": res.above_threshold, "features_expected": res.features_expected, "features_finite": res.features_finite,
                                  "warnings": res.warnings}
        except Exception as e:  # noqa: BLE001 - recorded, never hidden
            r["problems"].append(f"{type(e).__name__}: {e}")
            r["checks"]["exception_free"] = False
        r["passed"] = bool(r["checks"]) and all(r["checks"].values()) and not r["problems"]
        ok_all &= r["passed"]
        rows.append(r)

    rep = {"schema": "parkistep.phase6.artifact_verification/1", "artifacts_root": str(root), "n_manifests": len(manifests),
           "n_passed": sum(1 for r in rows if r["passed"]), "all_passed": bool(rows) and ok_all,
           "real_window_source": {"recording": rec, "participant": pid, "window_offset_s": a.window_offset_s},
           "experiment_ids": sorted({r["identity"]["experiment_id"] for r in rows if "identity" in r}), "artifacts": rows,
           "software_mismatch_in_any_artifact": any(r.get("software_mismatch") for r in rows),
           "software_mismatch_note": "if true, install requirements-lock.txt to reproduce with the training-time versions"}
    out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, indent=2, default=str))
    print(f"{rep['n_passed']}/{rep['n_manifests']} artifacts passed -> {out}")
    if rep["software_mismatch_in_any_artifact"]:
        print("WARNING: running package versions differ from training time (see software_mismatch); install requirements-lock.txt for exact reproduction")
    for r in rows:
        if not r["passed"]:
            print("FAIL", r["manifest"], r["problems"], [k for k, v in r["checks"].items() if not v])
    return 0 if rep["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
