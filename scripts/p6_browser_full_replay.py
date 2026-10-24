#!/usr/bin/env python
"""Phase 6 real-browser check: ONE complete replay of the default recording through the real dashboard, then compare what the UI
shows with the independent recomputation (reports/phase6/retrospective_recompute.json) and the saved Phase 3 replay summary.
    python scripts/p6_browser_full_replay.py [--out reports/phase6/browser_full_replay.json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from p6_browser_lib import ROOT, body, exceptions, metrics, num, goto_page, streamlit_server, wait_for  # noqa: E402
from p6_browser_failure_paths import btn, shot  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="reports/phase6/browser_full_replay.json")
    ap.add_argument("--timeout-s", type=int, default=240)
    a = ap.parse_args()
    rec = json.loads((ROOT / "reports/phase6/retrospective_recompute.json").read_text())
    exp = rec["independent"]; checks = {}

    def chk(k, ok, d=""):
        checks[k] = {"pass": bool(ok), "detail": str(d)[:300]}

    t0 = time.time()
    with streamlit_server("app/Home.py", {}) as (url, log, proc), sync_playwright() as p:
        b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1440, "height": 900}); errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
        goto_page(pg, url, "replay-analysis", "Start new replay")
        sel = metrics(pg); btn(pg, "btn_start").click()
        done = wait_for(pg, lambda: "Completed" in body(pg) and "3. Retrospective analysis" in body(pg), a.timeout_s, 1.5)
        wall = time.time() - t0
        time.sleep(2); m = metrics(pg); t = body(pg); shot(pg, "full_replay_completed")
        chk("replay reaches Completed and shows the retrospective section", done, f"{wall:.0f} s wall incl. server start")
        chk("the replayed recording is the one recorded in the independent recomputation", m.get("Recording") == rec["recording_id"], m.get("Recording"))
        chk("Observations processed equals observations offered (11k scale), none rejected", (num(m.get("Observations processed")) or 0) > 10000 and (num(m.get("Rejected")) or 0) == 0, m)
        ev = re.findall(r"Annotated events\s+(\d+)\s", t)
        chk("annotated events (shown only now) equal the independent count", bool(ev) and int(ev[-1]) == exp["annotated_events"], ev)
        chk("detected early matches independent recompute", re.search(rf"\b{exp['events_detected']} / {exp['annotated_events']}\b", t) is not None, "")
        chk("valid / false warnings match independent recompute", re.search(rf"\b{exp['valid_early_warnings']} / {exp['false_warnings']}\b", t) is not None, "")
        chk("excluded-overlap warning count stated and equal", f"{exp['warnings_excluded_overlap_freezing']} warning(s)" in t, "")
        chk("retrospective result is labelled in-sample", "in-sample" in t.lower())
        chk("future-data violations reported as 0", m.get("Future-data violations") == "0", m.get("Future-data violations"))
        chk("no Streamlit exception / JS error", exceptions(pg) == 0 and not errs, errs)
        out = {"selection_before_start": sel, "metrics_after_completion": m, "wall_seconds_including_server_start": round(wall, 1)}
        b.close()
    rep = {"schema": "parkistep.phase6.browser_full_replay/1", "checks": checks, "all_passed": all(c["pass"] for c in checks.values()), **out}
    Path(a.out).write_text(json.dumps(rep, indent=2))
    print("ALL PASSED" if rep["all_passed"] else "FAILURES", len(checks), "checks")
    for k, v in checks.items():
        if not v["pass"]: print("  x", k, "|", v["detail"])
    return 0 if rep["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
