"""Phase 5 performance / resource measurement. Everything changeable is an argument; nothing is estimated - every number
in the output JSON was measured by this run on this machine.

  python scripts/measure_phase5.py --repetitions 3 --max-observations 3000 --out reports/phase5/performance_measurements.json
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/phase4.yaml")
    ap.add_argument("--repetitions", type=int, default=3)
    ap.add_argument("--max-observations", type=int, default=3000, help="cap on observations per measured replay (0 = whole recording)")
    ap.add_argument("--patient", default=None, help="default: the Phase 3 selection policy")
    ap.add_argument("--recording", default=None)
    ap.add_argument("--out", default="reports/phase5/performance_measurements.json")
    a = ap.parse_args()

    import psutil
    import pandas as pd
    import numpy as np
    from parkistep.dashboard.config import load_phase4
    from parkistep.dashboard.service import build_service

    proc = psutil.Process(os.getpid())
    rss = lambda: proc.memory_info().rss / 2 ** 20            # noqa: E731
    out: dict = {"environment": {"python": platform.python_version(), "platform": platform.platform(), "cpu_count": os.cpu_count(),
                                 "ram_gb": round(psutil.virtual_memory().total / 2 ** 30, 1), "pandas": pd.__version__, "numpy": np.__version__},
                 "parameters": vars(a), "measured": {}}
    m = out["measured"]
    cap = [f"replay.max_observations={a.max_observations}"] if a.max_observations else []

    t = time.perf_counter(); load_phase4(ROOT / a.config, ROOT); m["config_load_s"] = time.perf_counter() - t
    rss0 = rss()
    t = time.perf_counter(); svc = build_service(ROOT / a.config, ROOT, ["replay.persist_state=false"], cap); m["service_build_s"] = time.perf_counter() - t
    t = time.perf_counter(); svc.warm_up(); m["warm_up_s_(dataset_discovery+model_load)"] = time.perf_counter() - t
    rss_loaded = rss()
    if not svc.model_description().get("available"):
        print("model unavailable:", svc.model_description().get("problem")); return 2

    sel = svc.default_selection()
    if a.patient: sel["patient_id"] = a.patient
    if a.recording: sel["recording_id"] = a.recording
    runs = []
    for i in range(a.repetitions):
        f = svc.fork()
        t = time.perf_counter(); sess = f.create_session(sel["patient_id"], sel["recording_id"], replace=True); init_s = time.perf_counter() - t
        sess.start(); t0 = time.perf_counter(); r_before = rss(); n_ticks = 0; tick_s = []
        while not sess.is_finished:
            t = time.perf_counter(); f.advance(sess, "max"); tick_s.append(time.perf_counter() - t); n_ticks += 1
        wall = time.perf_counter() - t0
        c = sess.summary["counts"]; tm = sess.summary["timing"]
        runs.append({"twin_init_s": init_s, "replay_wall_s": wall, "observations": c["observations_processed"], "forecasts": c["predictions"],
                     "ms_per_observation": 1000 * wall / max(1, c["observations_processed"]),
                     "mean_inference_ms": tm["mean_inference_ms"], "max_inference_ms": tm["max_inference_ms"],
                     "ticks": n_ticks, "max_tick_s": max(tick_s), "rss_mb_before": r_before, "rss_mb_after": rss(),
                     "digest": sess.summary["determinism_digest"]})
    m["replays"] = runs
    m["digests_identical_across_repetitions"] = len({r["digest"] for r in runs}) == 1
    m["rss_mb"] = {"process_start": rss0, "after_model_and_data_load": rss_loaded, "end": rss(),
                   "growth_over_repetitions_mb": runs[-1]["rss_mb_after"] - runs[0]["rss_mb_after"]}
    m["sessions_retained_in_service"] = len(svc.sessions)
    Path(ROOT / a.out).parent.mkdir(parents=True, exist_ok=True)
    (ROOT / a.out).write_text(json.dumps(out, indent=2, default=float))
    print(json.dumps(out, indent=2, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
