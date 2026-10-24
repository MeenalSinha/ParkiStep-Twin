"""Phase 5 real-browser check (Playwright/Chromium). Start the dashboard first, in the same shell session:
    streamlit run app/Home.py --server.headless true --server.port 8765 &   then   python scripts/browser_pages_phase5.py
Base URL from env PARKISTEP_DASH_URL (default http://localhost:8765). Screenshots go to PARKISTEP_SHOT_DIR (default /tmp/shots)."""
import os
BASE=os.environ.get("PARKISTEP_DASH_URL","http://localhost:8765")
SHOTS=os.environ.get("PARKISTEP_SHOT_DIR","/tmp/shots")
os.makedirs(SHOTS,exist_ok=True)
import sys, time, json
from playwright.sync_api import sync_playwright
out={}
with sync_playwright() as p:
    try:
        b=p.chromium.launch()
    except Exception as e:
        print("LAUNCH_FAIL", str(e)[:400]); sys.exit(2)
    pg=b.new_page(viewport={"width":1440,"height":900})
    errs=[]
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(""+BASE+"/", wait_until="networkidle", timeout=90000)
    pg.wait_for_selector('[data-testid="stSidebar"]', timeout=60000)
    time.sleep(3)
    pg.screenshot(path=SHOTS+"/overview.png", full_page=True)
    links=pg.locator('[data-testid="stSidebarNav"] a').all_inner_texts()
    out["nav"]=links
    for l in links:
        pg.locator('[data-testid="stSidebarNav"] a', has_text=l).first.click()
        time.sleep(4)
        exc=pg.locator('[data-testid="stException"]').count()
        out[l]={"exceptions":exc,"text_len":len(pg.inner_text("body"))}
        pg.screenshot(path=f"{SHOTS}/{l.replace(' ','_')}.png", full_page=True)
    out["pageerrors"]=errs
    b.close()
print(json.dumps(out,indent=1))
