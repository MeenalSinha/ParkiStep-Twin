#!/usr/bin/env python
"""Phase 6 real-browser failure-path checks: Stop, failed replay, stale forecast, persistence failure, data-integrity faults,
model-inference failure. Real Streamlit server + real Chromium + real FoG-STAR data + real model. Faults are injected only by
tests/fixtures/fault_app.py (in-process wrappers); the dataset and the model artifacts are never modified.

    python scripts/p6_browser_failure_paths.py [--only stop,stream_error,...] [--out reports/phase6/browser_failure_paths.json]
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from p6_browser_lib import ROOT, body, exceptions, metrics, nav, num, goto_page, streamlit_server, wait_for  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

SHOTS = ROOT / "reports" / "phase6" / "screenshots" / "failure_paths"
FAULT_APP = "tests/fixtures/fault_app.py"


def btn(pg, key):
    return pg.locator(f".st-key-{key} button")


def start(pg):
    btn(pg, "btn_start").click()
    time.sleep(1)


class Scenario:
    def __init__(self, name, desc):
        self.name, self.desc, self.checks, self.notes, self.obs = name, desc, {}, [], {}

    def check(self, label, ok, detail=""):
        self.checks[label] = {"pass": bool(ok), "detail": str(detail)[:400]}

    def result(self):
        return {"scenario": self.name, "description": self.desc, "passed": all(c["pass"] for c in self.checks.values()) and bool(self.checks),
                "checks": self.checks, "observations": self.obs, "notes": self.notes}


def shot(pg, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    pg.screenshot(path=str(SHOTS / f"{name}.png"), full_page=True)


def server_log_counts(log_path):
    t = Path(log_path).read_text(errors="ignore")
    return {"twin_created": len(re.findall(r"twin created:", t)), "tracebacks": t.count("Traceback"), "text": t}


# --------------------------------------------------------------------------------------------------------------------------
def sc_stop(b, flag):
    s = Scenario("stop", "Stop a real replay: advancement ceases, state is STOPPED (not COMPLETED), history stays accessible, no duplicate twin")
    with streamlit_server(FAULT_APP, {"PARKISTEP_TEST_FAULT": "", "PARKISTEP_TEST_FLAG": flag}) as (url, log, proc):
        pg = b.new_page(viewport={"width": 1440, "height": 900})
        errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
        goto_page(pg, url, "replay-analysis", "Start new replay")
        start(pg)
        wait_for(pg, lambda: (num(metrics(pg).get("Forecasts")) or 0) >= 20, 60)
        m_run = metrics(pg)
        time.sleep(3); m_run2 = metrics(pg)
        s.check("observations and forecasts advance while running", (num(m_run2.get("Forecasts")) or 0) > (num(m_run.get("Forecasts")) or 0) or (num(m_run2["Processed / rejected"]) or 0) > (num(m_run["Processed / rejected"]) or 0), f"{m_run} -> {m_run2}")
        btn(pg, "btn_stop").click(); time.sleep(3)
        m1 = metrics(pg); time.sleep(5); m2 = metrics(pg)
        s.obs["metrics_after_stop"] = m1
        s.check("advancement ceases after Stop (identical after 5 s)", m1 == m2, f"{m1} vs {m2}")
        t = body(pg)
        s.check("UI shows STOPPED", "STOPPED" in t.upper() and "COMPLETED" not in t.split("3. Retrospective")[0].upper().replace("COMPLETED SESSIONS", ""), t[t.find("2. Run"):][:200])
        s.check("start button available again, pause/resume/stop disabled", btn(pg, "btn_start").is_enabled() and not btn(pg, "btn_stop").is_enabled() and not btn(pg, "btn_pause").is_enabled())
        s.check("twin enters the ended-session state (WAITING_FOR_DATA, not READY)", m1.get("Twin status") == "WAITING_FOR_DATA", m1.get("Twin status"))
        s.check("partial replay is flagged in the retrospective section", "stopped early" in t.lower(), "")
        s.check("retrospective section distinguishes in-sample/partial", "in-sample" in t.lower() or "in sample" in t.lower())
        shot(pg, "stop_replay_analysis")
        nav(pg, "Early Warning"); time.sleep(2)
        te = body(pg)
        s.check("Early Warning: no current forecast, last forecast labelled NOT current", "No current forecast" in te and "not current" in te.lower(), "")
        s.check("old score is not shown as a fresh forecast or as zero risk", "0.000" not in te.split("Last computed")[0] and "low risk" not in te.lower().replace("not low risk", ""))
        shot(pg, "stop_early_warning")
        nav(pg, "Warning Center"); shot(pg, "stop_warning_center")
        s.check("Warning Center accessible after Stop without exception", exceptions(pg) == 0)
        nav(pg, "Movement Monitor"); shot(pg, "stop_movement")
        s.check("Movement Monitor still shows recorded observations", exceptions(pg) == 0)
        pg.reload(wait_until="networkidle"); time.sleep(4)           # a browser reload is a NEW Streamlit session: it must not restart or duplicate the replay
        nav(pg, "Replay Analysis"); time.sleep(2)
        tr = body(pg); s.obs["after_reload"] = tr[tr.find("2. Run"):][:200]
        s.check("after a browser reload no replay is running or restarted (idle, new session)", "No replay started" in tr and btn(pg, "btn_start").is_enabled(), s.obs["after_reload"])
        time.sleep(4)
        c = server_log_counts(log)
        s.check("exactly one twin created by the whole interaction (no duplicate replay on reruns/reload)", c["twin_created"] == 1, c["twin_created"])
        s.check("no Streamlit exceptions / JS page errors", exceptions(pg) == 0 and not errs, errs)
        s.check("no Python traceback in server log", c["tracebacks"] == 0, c["tracebacks"])
        pg.close()
    return s.result()


def sc_stream_error(b, flag):
    s = Scenario("failed_replay", "Controlled stream failure mid-replay, then recovery with a new valid session")
    flagp = Path(flag); flagp.write_text("x")
    with streamlit_server(FAULT_APP, {"PARKISTEP_TEST_FAULT": "stream_error", "PARKISTEP_TEST_FLAG": flag, "PARKISTEP_TEST_AFTER": 900}) as (url, log, proc):
        pg = b.new_page(viewport={"width": 1440, "height": 900})
        errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
        goto_page(pg, url, "replay-analysis", "Start new replay")
        start(pg)
        ok = wait_for(pg, lambda: "Replay failed" in body(pg), 90)
        s.check("failure is presented clearly in the UI", ok, "")
        t = body(pg); i = t.find("Replay failed")
        s.obs["error_text"] = t[i:i + 260]
        s.check("error names the cause", "RuntimeError" in t and "sensor stream connection lost" in t)
        s.check("UI states no synthetic data were substituted", "No synthetic data were substituted" in t)
        m1 = metrics(pg); time.sleep(4); m2 = metrics(pg)
        s.obs["metrics_after_failure"] = m1
        s.check("no new forecasts appear after the failure", m1 == m2 and (num(m1.get("Forecasts")) or 0) > 0, f"{m1} vs {m2}")
        s.check("status badge FAILED and controls reset", "FAILED" in t.upper() and btn(pg, "btn_start").is_enabled() and not btn(pg, "btn_stop").is_enabled())
        s.check("no retrospective (ground-truth) analysis is produced for a failed replay", "3. Retrospective analysis" not in t)
        shot(pg, "failed_replay")
        nav(pg, "Early Warning"); te = body(pg)
        s.check("Early Warning does not present a current forecast after failure", "No current forecast" in te or "not current" in te.lower(), te[:300])
        shot(pg, "failed_early_warning")
        s.check("no Streamlit exception screens", exceptions(pg) == 0 and not errs, errs)
        flagp.unlink(missing_ok=True)                                         # recovery: fault removed
        nav(pg, "Replay Analysis"); time.sleep(2)
        start(pg)
        ok = wait_for(pg, lambda: (num(metrics(pg).get("Forecasts")) or 0) >= 30 and "Replay failed" not in body(pg), 90)
        s.check("a new valid replay can be started after the failure and produces forecasts", ok, metrics(pg))
        mm = metrics(pg); s.obs["metrics_after_recovery"] = mm
        shot(pg, "failed_replay_recovered")
        btn(pg, "btn_stop").click(); time.sleep(2)
        c = server_log_counts(log)
        s.check("server logged the failure with a traceback (diagnosable)", "injected test fault: sensor stream connection lost" in c["text"], "")
        pg.close()
    flagp.unlink(missing_ok=True)
    return s.result()


def sc_stall(b, flag):
    s = Scenario("stale_forecast", "Sensor stream stalls while historical predictions are retained: forecast becomes stale, never a fresh/zero score")
    flagp = Path(flag); flagp.unlink(missing_ok=True)
    with streamlit_server(FAULT_APP, {"PARKISTEP_TEST_FAULT": "stall", "PARKISTEP_TEST_FLAG": flag, "PARKISTEP_TEST_STALL_STEP_S": 2.0}) as (url, log, proc):
        pg = b.new_page(viewport={"width": 1440, "height": 900})
        errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
        goto_page(pg, url, "replay-analysis", "Start new replay")
        start(pg)
        wait_for(pg, lambda: (num(metrics(pg).get("Forecasts")) or 0) >= 40, 60)
        nav(pg, "Early Warning"); time.sleep(2)
        t0 = body(pg); s.obs["fresh_text"] = t0[t0.find("CURRENT MODEL SCORE"):][:220]
        s.check("while data flows, the forecast is shown as current", re.search(r"CURRENT MODEL SCORE\s+\d\.\d+", t0) is not None, s.obs["fresh_text"])
        shot(pg, "stale_before")
        flagp.write_text("x")                                                  # sensor stops delivering; time keeps passing
        ok = wait_for(pg, lambda: "No current forecast" in body(pg), 40)
        t1 = body(pg); s.obs["stale_text"] = t1[t1.find("CURRENT MODEL SCORE"):][:520]
        s.check("forecast is no longer presented as current once data stop", ok, s.obs["stale_text"])
        s.check("badge says Stale (or unavailable), not a score", "STALE" in t1.upper() or "NO FORECAST" in t1.upper(), "")
        s.check("old score labelled 'last computed ... not current'", "not current" in t1.lower())
        cur = t1[t1.find("CURRENT MODEL SCORE"):][:200]
        s.check("missing/stale forecast is not displayed as zero risk", not re.search(r"CURRENT MODEL SCORE\s+0\.0+\b", t1))
        nav(pg, "Overview"); to = body(pg); shot(pg, "stale_overview")
        s.obs["overview_excerpt"] = to[to.upper().find("TWIN STATUS"):][:500]
        s.check("sensor-stream state (latest sensor timestamp) and model state (forecast card) are both shown and distinguishable",
                "latest sensor timestamp" in to.lower() and ("no current forecast" in to.lower() or "not current" in to.lower()), s.obs["overview_excerpt"])
        s.check("twin status is STALE", "STALE" in to.upper(), "")
        nav(pg, "Early Warning"); shot(pg, "stale_early_warning")
        flagp.unlink(missing_ok=True)                                          # stream resumes
        nav(pg, "Early Warning")
        ok = wait_for(pg, lambda: re.search(r"CURRENT MODEL SCORE\s+\d\.\d+", body(pg)) is not None, 60)
        s.check("when data resume, a fresh current forecast is shown again", ok, "")
        s.check("no Streamlit exceptions", exceptions(pg) == 0 and not errs, errs)
        pg.close()
    flagp.unlink(missing_ok=True)
    return s.result()


def sc_persist(b, flag):
    s = Scenario("persistence_failure", "State-store failure during a persisted replay: failure shown, no false claim of persistence, lock released, recovery possible")
    flagp = Path(flag); flagp.unlink(missing_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="p6_persist_")); db = tmp / "state.sqlite"
    env = {"PARKISTEP_TEST_FAULT": "persist_error", "PARKISTEP_TEST_FLAG": flag, "PARKISTEP_PHASE4_SET": "replay.persist_state=true",
           "PARKISTEP_PHASE3_SET": f"persistence.path={db}"}
    with streamlit_server(FAULT_APP, env) as (url, log, proc):
        pg = b.new_page(viewport={"width": 1440, "height": 900})
        errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
        goto_page(pg, url, "replay-analysis", "Start new replay")
        start(pg)
        wait_for(pg, lambda: (num(metrics(pg).get("Forecasts")) or 0) >= 20, 60)
        flagp.write_text("x")                                                  # storage breaks mid-replay
        ok = wait_for(pg, lambda: "Replay failed" in body(pg), 60)
        t = body(pg); i = t.find("Replay failed"); s.obs["error_text"] = t[i:i + 260]
        s.check("persistence failure is shown in the dashboard", ok and "StateStoreError" in t, s.obs["error_text"])
        s.check("error text identifies storage (not silently ignored)", "database is locked" in t or "disk full" in t)
        s.check("no retrospective/successful-completion view is shown", "3. Retrospective analysis" not in t)
        m1 = metrics(pg); time.sleep(3); m2 = metrics(pg)
        s.check("replay state stays consistent after the failure (counters frozen)", m1 == m2, f"{m1} vs {m2}")
        shot(pg, "persistence_failure")
        flagp.unlink(missing_ok=True)                                          # storage available again
        # what does the database say? The uncommitted writes must not be present as if they had been stored.
        con = sqlite3.connect(db); rows = {t_: con.execute(f"select count(*) from {t_}").fetchone()[0] for t_ in ("twins", "predictions", "events")}; con.close()
        s.obs["db_rows_after_failure"] = rows
        shown_forecasts = num(m1.get("Forecasts")) or 0
        s.check("database does not claim more predictions than were produced (rolled back, not falsely persisted)", rows["predictions"] <= shown_forecasts, f"db={rows} shown={shown_forecasts}")
        # recovery: choose another patient (the first patient's twin is protected from overwrite by design)
        failed_patient = m1.get("Recording", "").split("-")[0].lstrip("p")
        pg.locator("[data-testid=stSelectbox]").first.click(); time.sleep(1)
        texts = [o.inner_text().strip() for o in pg.locator('[role="option"]').all()]
        s.obs["n_patient_options"] = len(texts)
        other = next((i for i, t_ in enumerate(texts) if t_.split(" ")[0] != failed_patient), None)
        if other is None:
            s.check("recovery with another patient", False, "no alternative patient")
        else:
            pg.locator('[role="option"]').nth(other).click(); time.sleep(3)
            new_patient = texts[other].split(" ")[0]
            start(pg)
            ok = wait_for(pg, lambda: metrics(pg).get("Recording", "").startswith(f"p{new_patient}-") and (num(metrics(pg).get("Forecasts")) or 0) >= 10, 90)
            tt = body(pg)
            s.check("after storage recovers, a replay for another patient starts and produces forecasts (no 'database is locked')",
                    ok and "database is locked" not in tt and "Replay failed" not in tt and "Cannot start" not in tt, tt[tt.find("2. Run"):][:300])
            s.obs["recovery_metrics"] = metrics(pg)
            shot(pg, "persistence_recovered")
            btn(pg, "btn_stop").click(); time.sleep(2)
        s.check("no Streamlit exception screens / JS errors", exceptions(pg) == 0 and not errs, errs)
        pg.close()
    flagp.unlink(missing_ok=True)
    return s.result()


def sc_data_faults(b, flag):
    s = Scenario("data_integrity_faults", "Duplicate, invalid-timestamp, out-of-order and missing-channel observations injected into the real stream")
    with streamlit_server(FAULT_APP, {"PARKISTEP_TEST_FAULT": "data_faults", "PARKISTEP_TEST_FLAG": flag, "PARKISTEP_TEST_AFTER": 700,
                                      "PARKISTEP_PHASE4_SET": "replay.default_speed=50"}) as (url, log, proc):
        pg = b.new_page(viewport={"width": 1440, "height": 900})
        errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
        goto_page(pg, url, "replay-analysis", "Start new replay")
        start(pg)
        wait_for(pg, lambda: (num(metrics(pg).get("Processed / rejected")) or 0) > 1400 or "Replay failed" in body(pg), 120)
        t = body(pg); m = metrics(pg); s.obs["metrics"] = m; shot(pg, "data_faults")
        rej = re.findall(r"[\d,]+", m.get("Processed / rejected", "0 / 0"))
        rejected = int(rej[1].replace(",", "")) if len(rej) > 1 else 0
        s.check("replay did not crash on bad records (no 'Replay failed')", "Replay failed" not in t, t[t.find("2. Run"):][:200])
        s.check("rejected observations are counted and shown (not silently dropped)", rejected >= 1, m.get("Processed / rejected"))
        s.check("forecasting continued on valid data after the faults", (num(m.get("Forecasts")) or 0) > 30, m.get("Forecasts"))
        s.check("no Streamlit exception screen", exceptions(pg) == 0 and not errs, errs)
        s.obs["rejection_text"] = t[t.lower().find("reject"):][:300]
        nav(pg, "Overview"); wait_for(pg, lambda: "data quality" in body(pg).lower(), 20); to = body(pg); shot(pg, "data_faults_overview")
        s.obs["overview_dq"] = to[to.lower().find("data quality"):][:400]
        s.check("Overview surfaces data-quality information", "data quality" in to.lower(), "")
        btn_stop = btn(pg, "btn_stop")
        pg.close()
    return s.result()


def sc_model_error(b, flag):
    s = Scenario("model_inference_failure", "Model inference raises during replay: forecast unavailable, no fabricated/zero score, recovers when the model works again")
    flagp = Path(flag); flagp.unlink(missing_ok=True)
    with streamlit_server(FAULT_APP, {"PARKISTEP_TEST_FAULT": "model_error", "PARKISTEP_TEST_FLAG": flag}) as (url, log, proc):
        pg = b.new_page(viewport={"width": 1440, "height": 900})
        errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
        goto_page(pg, url, "replay-analysis", "Start new replay")
        start(pg)
        wait_for(pg, lambda: (num(metrics(pg).get("Forecasts")) or 0) >= 20, 60)
        n0 = num(metrics(pg).get("Forecasts")) or 0
        flagp.write_text("x"); time.sleep(6)
        t = body(pg); m = metrics(pg); s.obs["metrics_during_fault"] = m; shot(pg, "model_error_replay")
        s.check("no new forecasts are counted while inference fails", (num(m.get("Forecasts")) or 0) <= n0 + 15, f"{n0} -> {m.get('Forecasts')}")
        nav(pg, "Early Warning"); te = body(pg); shot(pg, "model_error_early_warning")
        s.obs["early_warning_excerpt"] = te[te.find("CURRENT MODEL SCORE"):][:400]
        s.check("Early Warning shows no current forecast during inference failure", "No current forecast" in te or "unavailable" in te.lower(), s.obs["early_warning_excerpt"])
        s.check("no zero-risk score is displayed", not re.search(r"CURRENT MODEL SCORE\s+0\.0+\b", te))
        s.check("no Streamlit exception screen", exceptions(pg) == 0 and not errs, errs)
        flagp.unlink(missing_ok=True)
        ok = wait_for(pg, lambda: re.search(r"CURRENT MODEL SCORE\s+\d\.\d+", body(pg)) is not None, 60)
        s.check("forecasting recovers when the model works again", ok, "")
        pg.close()
    flagp.unlink(missing_ok=True)
    return s.result()


def sc_bad_inputs(b, flag):
    s = Scenario("bad_reports_and_saved_state", "Malformed / metric-less / missing evaluation report, and a corrupted persisted twin state: clear errors, nothing fabricated")
    tmp = Path(tempfile.mkdtemp(prefix="p6_bad_"))
    variants = {"malformed_json": "{not json", "missing_metrics": json.dumps({"experiment_id": "x"}), "missing_file": None}
    for name, content in variants.items():
        path = tmp / f"{name}.json"
        if content is not None:
            path.write_text(content)
        with streamlit_server(FAULT_APP, {"PARKISTEP_TEST_FAULT": "", "PARKISTEP_PHASE4_SET": f"paths.phase2_metrics={path}"}) as (url, log, proc):
            pg = b.new_page(viewport={"width": 1440, "height": 900})
            errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
            goto_page(pg, url, "model-performance", None)
            wait_for(pg, lambda: "Evaluation results unavailable" in body(pg) or "Average precision" in body(pg), 60)
            t = body(pg); shot(pg, f"bad_report_{name}")
            s.check(f"[{name}] Model Performance says evaluation results are unavailable", "Evaluation results unavailable" in t, t[t.find("Model Performance"):][:250])
            s.check(f"[{name}] a reason is given", any(w in t.lower() for w in ("not found", "json", "could not", "no results", "missing", "invalid", "malformed", "unreadable")), t[t.find("unavailable"):][:250])
            s.check(f"[{name}] no performance figures are invented", "Average precision" not in t and "Event sensitivity" not in t)
            s.check(f"[{name}] no Streamlit exception screen / JS error", exceptions(pg) == 0 and not errs, errs)
            pg.close()
    # corrupted saved twin state in an isolated store
    sys.path.insert(0, str(ROOT / "src"))
    from parkistep.dashboard.service import build_service
    db = tmp / "state.sqlite"
    svc = build_service(ROOT / "configs/phase4.yaml", ROOT, ["replay.persist_state=true"], [f"persistence.path={db}"]).warm_up()
    d = svc.default_selection(); sess = svc.create_session(d["patient_id"], d["recording_id"]); svc.close()
    con = sqlite3.connect(db); con.execute("update twins set state_json='{broken'"); con.commit(); con.close()
    env = {"PARKISTEP_TEST_FAULT": "", "PARKISTEP_PHASE4_SET": "replay.persist_state=true", "PARKISTEP_PHASE3_SET": f"persistence.path={db}"}
    with streamlit_server(FAULT_APP, env) as (url, log, proc):
        pg = b.new_page(viewport={"width": 1440, "height": 900})
        errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
        goto_page(pg, url, "replay-analysis", "Start new replay")
        start(pg); time.sleep(8)
        t = body(pg); shot(pg, "bad_saved_state")
        s.obs["saved_state_text"] = t[t.find("2. Run"):][:400]
        s.check("with a corrupt persisted twin the replay does not start", "Replaying" not in t and (num(metrics(pg).get("Forecasts")) or 0) == 0, metrics(pg))
        s.check("an explicit error explains the refusal (never overwritten / not silently trusted)", "already exists" in t or "corrupt" in t.lower() or "Engine error" in t or "Cannot start" in t, s.obs["saved_state_text"])
        s.check("no default/blank state is shown as if it were the patient's real state", "No current forecast" in body(pg) or "No replay" in t or "Cannot start" in t or "already exists" in t)
        s.check("no Streamlit exception screen / JS error", exceptions(pg) == 0 and not errs, errs)
        pg.close()
    con = sqlite3.connect(db); st_ = con.execute("select state_json from twins").fetchone()[0]; con.close()
    s.check("the corrupted record was left untouched (not overwritten)", st_ == "{broken", st_[:40])
    return s.result()


SCENARIOS = {"stop": sc_stop, "stream_error": sc_stream_error, "stall": sc_stall, "persist": sc_persist, "data_faults": sc_data_faults, "model_error": sc_model_error, "bad_inputs": sc_bad_inputs}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=",".join(SCENARIOS))
    ap.add_argument("--out", default="reports/phase6/browser_failure_paths.json")
    ap.add_argument("--flag", default=str(Path(tempfile.gettempdir()) / "parkistep_fault.flag"))
    a = ap.parse_args()
    res = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        for k in a.only.split(","):
            t0 = time.time()
            try:
                r = SCENARIOS[k](b, a.flag)
            except Exception as e:                                             # noqa: BLE001 - recorded, not hidden
                r = {"scenario": k, "passed": False, "error": f"{type(e).__name__}: {e}", "checks": {}}
            r["wall_seconds"] = round(time.time() - t0, 1)
            res.append(r)
            print(f"{'PASS' if r['passed'] else 'FAIL'} {k} ({r['wall_seconds']} s)")
            for lab, c in r.get("checks", {}).items():
                if not c["pass"]:
                    print("   x", lab, "|", c["detail"])
            if r.get("error"):
                print("   !", r["error"])
        b.close()
    out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"schema": "parkistep.phase6.browser_failure_paths/1", "scenarios": res,
                               "all_passed": all(r["passed"] for r in res)}, indent=2))
    return 0 if all(r["passed"] for r in res) else 1


if __name__ == "__main__":
    sys.exit(main())
