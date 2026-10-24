"""Phase 5 real-browser check (Playwright/Chromium). Start the dashboard first, in the same shell session:
    streamlit run app/Home.py --server.headless true --server.port 8765 &   then   python scripts/browser_replay_phase5.py
Base URL from env PARKISTEP_DASH_URL (default http://localhost:8765). Screenshots go to PARKISTEP_SHOT_DIR (default /tmp/shots)."""
import os
BASE=os.environ.get("PARKISTEP_DASH_URL","http://localhost:8765")
SHOTS=os.environ.get("PARKISTEP_SHOT_DIR","/tmp/shots")
os.makedirs(SHOTS,exist_ok=True)
import time, json, re
from playwright.sync_api import sync_playwright
log=[]
def snap(pg,tag):
    t=pg.inner_text("body")
    exc=pg.locator('[data-testid="stException"]').count()
    log.append({"tag":tag,"exceptions":exc,"excerpt":re.sub(r"\s+"," ",t[t.find("2. Run"):][:700])})
with sync_playwright() as p:
    b=p.chromium.launch(); pg=b.new_page(viewport={"width":1440,"height":900})
    errs=[]; pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(""+BASE+"/replay-analysis", wait_until="networkidle", timeout=90000)
    pg.wait_for_selector("text=Start new replay", timeout=60000); time.sleep(2)
    pg.get_by_role("button", name="Start new replay").click(); time.sleep(6); snap(pg,"after_start")
    pg.screenshot(path=SHOTS+"/replay_running.png", full_page=True)
    pg.get_by_role("button", name="Pause").click(); time.sleep(3); snap(pg,"paused_a"); time.sleep(4); snap(pg,"paused_b")
    pg.get_by_role("button", name="Resume").click(); time.sleep(5); snap(pg,"resumed")
    # navigate away mid-replay and back
    pg.locator('[data-testid="stSidebarNav"] a', has_text="Movement Monitor").first.click(); time.sleep(4); snap(pg,"movement_midreplay")
    pg.screenshot(path=SHOTS+"/movement_running.png", full_page=True)
    pg.locator('[data-testid="stSidebarNav"] a', has_text="Replay Analysis").first.click(); time.sleep(3)
    for i in range(24):
        time.sleep(5)
        t=pg.inner_text("body")
        if "Retrospective" in t or "completed" in t.lower(): break
    snap(pg,"end_wait"); pg.screenshot(path=SHOTS+"/replay_end.png", full_page=True)
    log.append({"pageerrors":errs}); b.close()
print(json.dumps(log,indent=1))
