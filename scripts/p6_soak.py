#!/usr/bin/env python
"""Phase 6 resource-reliability (soak) test of the dashboard service with the real data and real model.

Repeats replay cycles of four kinds (complete, stop-midway, injected stream failure, persisted-to-SQLite) and samples after every
cycle: process RSS, thread count, open file descriptors, open SQLite handles, number of live sessions, and the size of the
twin's prediction history / sensor buffer. Fits a least-squares trend of RSS over the post-warm-up cycles and compares it with a
configurable limit. Everything is configurable; the report states the duration, counts and its own limitations.

    python scripts/p6_soak.py --cycles 24 --max-obs 1500 --warmup 4 --max-rss-slope-mb-per-cycle 2.0 --max-rss-growth-mb 120
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402
import psutil  # noqa: E402

import parkistep.dashboard.service as svc_mod  # noqa: E402
from parkistep.dashboard.replay_session import SessionStatus  # noqa: E402
from parkistep.dashboard.service import build_service  # noqa: E402


class FailingStream:
    def __init__(self, inner, after):
        self.inner, self.after = inner, after

    def describe(self):
        return self.inner.describe()

    def records(self):
        for i, r in enumerate(self.inner.records()):
            if i >= self.after:
                raise RuntimeError("soak: injected stream failure")
            yield r


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycles", type=int, default=24)
    ap.add_argument("--max-obs", type=int, default=1500)
    ap.add_argument("--warmup", type=int, default=4, help="cycles excluded from the trend (imports, caches, allocator warm-up)")
    ap.add_argument("--max-rss-slope-mb-per-cycle", type=float, default=2.0)
    ap.add_argument("--max-rss-growth-mb", type=float, default=120.0)
    ap.add_argument("--max-sessions", type=int, default=None, help="default: the dashboard's configured session cap")
    ap.add_argument("--out", default="reports/phase6/soak_report.json")
    a = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="p6_soak_"))
    svc = build_service(ROOT / "configs/phase4.yaml", ROOT, [], [f"replay.max_observations={a.max_obs}"]).warm_up()
    svc_p = build_service(ROOT / "configs/phase4.yaml", ROOT, ["replay.persist_state=true"],
                          [f"replay.max_observations={a.max_obs}", f"persistence.path={tmp / 'soak.sqlite'}"]).warm_up()
    pats = [p["patient_id"] for p in svc.list_patients() if svc.list_recordings(p["patient_id"])]
    proc = psutil.Process(os.getpid())
    kinds = ["complete", "stop", "fail", "persist"]
    rows, t0 = [], time.time()
    orig_build = svc_mod.build_replay_stream
    persisted_used: set[str] = set()
    for i in range(a.cycles):
        kind = kinds[i % 4]
        pid = pats[i % len(pats)]                                    # data-driven: cycle through the real patients
        rid = svc.list_recordings(pid)[0]["recording_id"]
        use = svc_p if kind == "persist" else svc
        if kind == "persist" and pid in persisted_used:              # persisted twins are never overwritten by design -> fresh patient
            cand = [p for p in pats if p not in persisted_used]
            if not cand:
                continue
            pid = cand[0]; rid = svc.list_recordings(pid)[0]["recording_id"]
        if kind == "persist":
            persisted_used.add(pid)
        if kind == "fail":
            svc_mod.build_replay_stream = lambda *x, **k: (lambda st: (FailingStream(st[0], 400), st[1]))(orig_build(*x, **k))
        try:
            sess = use.create_session(pid, rid, replace=True)
            sess.start()
            if kind == "stop":
                use.advance(sess, "max") if False else sess.advance(max_observations=600)
                sess.stop()
            else:
                while not sess.is_finished:
                    sess.advance(sim_seconds=5.0)
            status = sess.status.value
            hist = len(getattr(sess.engine, "_predictions", {}) or {}) if hasattr(sess.engine, "_predictions") else None
            n_pred = sess.n_predictions
        finally:
            svc_mod.build_replay_stream = orig_build
        gc.collect()
        try:
            n_sqlite = sum(1 for f in proc.open_files() if f.path.endswith((".sqlite", ".sqlite-journal", ".db")))
        except Exception:                                            # noqa: BLE001
            n_sqlite = None
        rows.append({"cycle": i, "kind": kind, "patient": pid, "recording": rid, "status": status, "predictions": n_pred,
                     "rss_mb": round(proc.memory_info().rss / 1e6, 1), "threads": proc.num_threads(), "fds": proc.num_fds(),
                     "open_sqlite_files": n_sqlite, "live_sessions": len(svc.sessions) + len(svc_p.sessions), "t_s": round(time.time() - t0, 1)})
        print(rows[-1])

    post = [r for r in rows if r["cycle"] >= a.warmup]
    x = np.array([r["cycle"] for r in post], float); y = np.array([r["rss_mb"] for r in post], float)
    slope = float(np.polyfit(x, y, 1)[0]) if len(post) > 2 else None
    growth = float(y[-1] - y[0]) if len(post) else None
    by_kind = {k: [r["rss_mb"] for r in post if r["kind"] == k] for k in kinds}
    kind_slope = {k: (float(np.polyfit(range(len(v)), v, 1)[0]) if len(v) > 2 else None) for k, v in by_kind.items()}
    thr0 = post[0]["threads"] if post else None; fd0 = post[0]["fds"] if post else None
    keep = int(svc.s4["replay"]["keep_completed_sessions"])
    sess_cap = a.max_sessions or 2 * (keep + 1)          # two services (plain + persisted), each keeps `keep` finished + the current one
    checks = {
        "rss_slope_within_limit": slope is not None and slope <= a.max_rss_slope_mb_per_cycle,
        "rss_total_growth_within_limit": growth is not None and growth <= a.max_rss_growth_mb,
        "thread_count_stable": all(r["threads"] <= thr0 + 2 for r in post),
        "fd_count_stable": all(r["fds"] <= fd0 + 6 for r in post),
        "open_sqlite_handles_bounded_by_session_retention (keep_completed_sessions + 1)": all((r["open_sqlite_files"] or 0) <= keep + 1 for r in post),
        "failed_cycles_ended_FAILED": all(r["status"].lower() == "failed" for r in rows if r["kind"] == "fail"),
        "stopped_cycles_ended_STOPPED": all(r["status"].lower() == "stopped" for r in rows if r["kind"] == "stop"),
        "completed_cycles_ended_COMPLETED": all(r["status"].lower() == "completed" for r in rows if r["kind"] in ("complete", "persist")),
        "live_sessions_bounded": max(r["live_sessions"] for r in rows) <= sess_cap,
    }
    rep = {"schema": "parkistep.phase6.soak/1", "parameters": vars(a), "cycles_run": len(rows), "duration_s": round(time.time() - t0, 1),
           "rss_slope_mb_per_cycle_after_warmup": slope, "rss_growth_mb_after_warmup": growth, "rss_slope_by_kind_mb_per_cycle": kind_slope,
           "rss_first_last_mb": [rows[0]["rss_mb"], rows[-1]["rss_mb"]], "checks": checks, "all_passed": all(checks.values()), "samples": rows,
           "limitations": ["service-level soak in one Python process (the Streamlit server adds its own overhead, sampled separately by the browser soak)",
                           f"{len(rows)} cycles of <= {a.max_obs} observations each; this bounds, but does not prove the absence of, slow leaks over hours or days",
                           "RSS is noisy (allocator/OS behaviour); the decision uses a fitted trend after warm-up, not single readings"]}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True); Path(a.out).write_text(json.dumps(rep, indent=2))
    print(json.dumps({k: rep[k] for k in ("cycles_run", "duration_s", "rss_slope_mb_per_cycle_after_warmup", "rss_growth_mb_after_warmup", "checks", "all_passed")}, indent=1))
    return 0 if rep["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
