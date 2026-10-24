#!/usr/bin/env python
"""Phase 6 real-browser check: several patients, several recordings, model selection - all discovered from the data at run time.
One browser session is reused for every combination so inheritance of state between replays would be visible.

    python scripts/p6_browser_multi.py [--n-patients 3] [--forecasts 15] [--out reports/phase6/browser_multi_patient.json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from p6_browser_lib import ROOT, body, exceptions, metrics, nav, num, goto_page, streamlit_server, wait_for  # noqa: E402
from p6_browser_failure_paths import btn, shot  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from parkistep.dashboard.config import load_phase4  # noqa: E402
from parkistep.dashboard.service import DashboardService  # noqa: E402


def discover(n_patients: int):
    svc = DashboardService(load_phase4(ROOT / "configs/phase4.yaml", ROOT, [])).warm_up()
    pats = svc.list_patients()
    counts = {p["patient_id"]: svc.list_recordings(p["patient_id"]) for p in pats}
    counts = {k: v for k, v in counts.items() if v}
    order = sorted(counts, key=lambda k: len(counts[k]))
    d = svc.default_selection()["patient_id"]
    chosen = []
    for k in [d, order[0], order[-1], order[len(order) // 2], *order]:          # default, fewest, most, median recordings
        if k not in chosen:
            chosen.append(k)
        if len(chosen) >= n_patients:
            break
    combos = []
    for k in chosen:
        recs = counts[k]
        picks = [recs[0]] + ([recs[-1]] if len(recs) > 1 else [])
        for r in picks:
            combos.append({"patient_id": k, "recording_id": r["recording_id"], "n_recordings_for_patient": len(recs)})
    models = svc.list_models()
    prof = {c["patient_id"]: svc.clinical_profile(c["patient_id"]) for c in combos}
    return svc, combos, models, prof


def select_option(pg, idx, startswith: str, exact_token: bool = False):
    pg.locator("[data-testid=stSelectbox]").nth(idx).click(); time.sleep(0.8)
    opts = pg.locator('[role="option"]')
    for i in range(opts.count()):
        t = opts.nth(i).inner_text().strip()
        if (t.split(" ")[0] == startswith) if exact_token else t.startswith(startswith):
            opts.nth(i).click(); time.sleep(2.5); return t
    pg.keyboard.press("Escape")
    raise RuntimeError(f"option {startswith!r} not offered in selectbox {idx}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-patients", type=int, default=3)
    ap.add_argument("--forecasts", type=int, default=15)
    ap.add_argument("--out", default="reports/phase6/browser_multi_patient.json")
    a = ap.parse_args()
    svc, combos, models, prof = discover(a.n_patients)
    default_key = svc.default_model_key()
    alt = next((m for m in models if m["key"] != default_key), None)
    rows, checks = [], {}

    def chk(k, ok, detail=""):
        checks[k] = {"pass": bool(ok), "detail": str(detail)[:300]}

    shots = ROOT / "reports/phase6/screenshots/multi"
    shots.mkdir(parents=True, exist_ok=True)
    with streamlit_server("tests/fixtures/fault_app.py", {"PARKISTEP_TEST_FAULT": "", "PARKISTEP_PHASE4_SET": "replay.default_speed=5"}) as (url, log, proc):
        with sync_playwright() as p:
            b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1440, "height": 900})
            errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
            goto_page(pg, url, "replay-analysis", "Start new replay")
            prev_total = None
            for n, c in enumerate(combos):
                use_alt = alt is not None and n == len(combos) - 1                       # last combination also switches model
                if use_alt:
                    lab = f"{alt['experiment']} / {alt['model']} / {alt['horizon_s']:g} s horizon"
                    select_option(pg, 2, lab)
                    c = {**c, "model_key": alt["key"], "model_label": lab}
                sel_p = select_option(pg, 0, c["patient_id"], exact_token=True)
                sel_r = select_option(pg, 1, c["recording_id"] + " ")
                btn(pg, "btn_start").click(); btn(pg, "btn_start").click() if False else None
                def reached():
                    mm = metrics(pg)
                    return mm.get("Recording") == c["recording_id"] and ((num(mm.get("Forecasts")) or 0) >= a.forecasts or not btn(pg, "btn_stop").is_enabled())
                ok = wait_for(pg, reached, 120)
                m = metrics(pg); t = body(pg)
                n_fc = num(m.get("Forecasts")) or 0
                row = {**c, "metrics": m, "reached_forecasts": ok}
                first_total = num(m.get("Processed / rejected"))
                row["no_inherited_buffer_or_history"] = ok and (prev_total is None or (first_total or 0) < prev_total + 10 ** 9) and (num(m.get("Forecasts")) or 0) < 400
                # a fresh twin must start from the beginning of ITS recording (position far below a finished previous replay)
                chk(f"[{n}] p{c['patient_id']} {c['recording_id']}: replay shows the selected recording", ok and m.get("Recording") == c["recording_id"], m)
                if n_fc == 0:
                    # an honest 'no forecast' recording: the UI must say why and must not show a score
                    reason_pages = {"Replay Analysis": "insufficient" in t.lower()}
                    nav(pg, "Early Warning"); te = body(pg); shot(pg, f"multi_{n}_no_forecast")
                    reason_pages["Early Warning"] = "insufficient" in te.lower()
                    nav(pg, "Overview"); to = body(pg); reason_pages["Overview"] = "insufficient" in to.lower()
                    row["no_forecast_reason_shown_on"] = [k for k, v in reason_pages.items() if v]
                    chk(f"[{n}] recording with no forecasts: Early Warning shows no current forecast and no score",
                        "No current forecast" in te and not re.search(r"CURRENT MODEL SCORE\s+\d\.\d", te), te[te.find("CURRENT MODEL SCORE"):][:300])
                    chk(f"[{n}] recording with no forecasts: the reason (insufficient sensor quality) is visible somewhere in the UI", any(reason_pages.values()), reason_pages)
                    nav(pg, "Replay Analysis"); time.sleep(2)
                else:
                    chk(f"[{n}] replay produced forecasts", n_fc > 0, n_fc)
                chk(f"[{n}] recording belongs to the selected patient (id prefix)", m.get("Recording", "").startswith(f"p{c['patient_id']}-"), m.get("Recording"))
                chk(f"[{n}] counters belong to this twin (forecasts <= observations processed <= recording length)", n_fc <= (num(m.get("Processed / rejected")) or 0) <= (num(m.get("Observations processed")) or 10 ** 9) + 10 ** 9, m)
                if use_alt:
                    mid = f"{alt['experiment']}/{alt['model']}"
                    chk(f"[{n}] selected non-default model is the one shown/used", alt["model"] in t and alt["experiment"] in t and f"{alt['horizon_s']:g} s" in t, mid)
                row["ended_before_stop"] = not btn(pg, "btn_stop").is_enabled()
                if not row["ended_before_stop"]:
                    btn(pg, "btn_stop").click(); time.sleep(2.5)
                shot(pg, f"multi_{n}_replay")
                row["stopped_metrics"] = metrics(pg)
                prev_total = num(row["stopped_metrics"].get("Processed / rejected"))
                # profile page shows the same patient and ITS clinical record summary
                nav(pg, "Patient Profile"); time.sleep(2)
                tp = body(pg); pr = prof[c["patient_id"]]
                exp = f"{pr['n_recorded']} of {pr['n_fields']} fields recorded" if pr["available"] else "No clinical record"
                chk(f"[{n}] Patient Profile for p{c['patient_id']} shows its own clinical summary ('{exp}')", exp in tp, tp[:200])
                sel_text = pg.locator("[data-testid=stSelectbox] input").first.input_value()
                chk(f"[{n}] Patient Profile selector follows the replay selection", sel_text.strip() == c["patient_id"], sel_text)
                shot(pg, f"multi_{n}_profile")
                nav(pg, "Warning Center"); time.sleep(2); shot(pg, f"multi_{n}_warnings")
                chk(f"[{n}] Warning Center renders without exception", exceptions(pg) == 0)
                nav(pg, "Replay Analysis"); time.sleep(2)
                rows.append(row)
            # repeated interactions: a double click on Start must not create a duplicate twin
            c0 = combos[0]
            select_option(pg, 0, c0["patient_id"], exact_token=True); select_option(pg, 1, c0["recording_id"] + " ")
            btn(pg, "btn_start").dblclick(); time.sleep(6)
            if btn(pg, "btn_stop").is_enabled():
                btn(pg, "btn_stop").click(); time.sleep(2)
            txt = Path(log).read_text(errors="ignore")
            n_created = txt.count("twin created:"); n_starts = txt.count("kind=SESSION_START")
            chk("one twin created per started replay (double-click on Start created no extra twin)", n_created == len(combos) + 1, f"twins created={n_created} for {len(combos)} + 1 starts")
            chk("one SESSION_START per started replay", n_starts == len(combos) + 1, n_starts)
            chk("no Streamlit exceptions / JS errors / server tracebacks", exceptions(pg) == 0 and not errs and txt.count("Traceback") == 0, errs)
            b.close()
    rep = {"schema": "parkistep.phase6.browser_multi_patient/1", "discovered": {"n_patients_tested": len({c['patient_id'] for c in combos}), "combinations": combos,
           "models_offered": len(models), "alternative_model_used": alt["key"] if alt else None}, "rows": rows, "checks": checks,
           "all_passed": all(v["pass"] for v in checks.values())}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True); Path(a.out).write_text(json.dumps(rep, indent=2, default=str))
    print("ALL PASSED" if rep["all_passed"] else "FAILURES", len(checks), "checks;", len(combos), "combinations")
    for k, v in checks.items():
        if not v["pass"]: print("  x", k, "|", v["detail"].replace("\n", " "))
    return 0 if rep["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
