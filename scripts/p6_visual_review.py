#!/usr/bin/env python
"""Phase 6 real-browser visual/responsive review: every dashboard page at several viewport sizes, from one live replay session.

Takes full-page screenshots (reports/phase6/screenshots/viewports/<w>x<h>/<page>.png) and runs OBJECTIVE checks per page and
viewport: no Streamlit exception, no horizontal page overflow, no metric/chart/table element clipped beyond the viewport edge,
smallest rendered text size, navigation reachable (sidebar visible or expandable). Human review of the screenshots is a separate
step and is recorded separately - these checks do not replace it.

    python scripts/p6_visual_review.py [--viewports 1920x1080,1440x900,...] [--forecasts 40]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from p6_browser_lib import ROOT, body, exceptions, metrics, num, streamlit_server, wait_for  # noqa: E402
from p6_browser_failure_paths import btn  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

PAGES = [("Overview", ""), ("Patient Profile", "patient-profile"), ("Movement Monitor", "movement-monitor"), ("Early Warning", "early-warning"),
         ("Warning Center", "warning-center"), ("Replay Analysis", "replay-analysis"), ("Model Performance", "model-performance")]

JS_CHECKS = """() => {
  const vw = window.innerWidth, out = {vw, docScrollW: document.documentElement.scrollWidth, bodyScrollW: document.body.scrollWidth};
  const main = document.querySelector('[data-testid=stMain]') || document.body;
  out.mainScrollW = main.scrollWidth; out.mainClientW = main.clientWidth;
  const sel = '[data-testid=stMetric],[data-testid=stPlotlyChart],[data-testid=stDataFrame],[data-testid=stAlert],button,[data-testid=stSelectbox],[data-testid=stSlider]';
  const clipped = [];
  document.querySelectorAll(sel).forEach(e => { const r = e.getBoundingClientRect(); if (r.width > 0 && (r.right > vw + 1 || r.left < -1)) clipped.push({tag: e.getAttribute('data-testid') || e.tagName, left: Math.round(r.left), right: Math.round(r.right)}); });
  out.clipped = clipped.slice(0, 8); out.n_clipped = clipped.length;
  let minFont = 99, tiny = [];
  document.querySelectorAll('[data-testid=stMain] p, [data-testid=stMain] span, [data-testid=stMain] label, [data-testid=stMain] li').forEach(e => {
    if (!e.childNodes.length || !Array.from(e.childNodes).some(n => n.nodeType === 3 && n.textContent.trim())) return;
    const r = e.getBoundingClientRect(); if (r.width === 0 || r.height === 0) return;
    const f = parseFloat(getComputedStyle(e).fontSize); if (f < minFont) minFont = f; if (f < 11) tiny.push(e.textContent.trim().slice(0, 30)); });
  const trunc = [];
  document.querySelectorAll('[data-testid=stMetricValue] *, [data-testid=stMetricLabel] *, .pk-big, .pk-sub').forEach(e => {
    if (e.scrollWidth > e.clientWidth + 1 && getComputedStyle(e).textOverflow === 'ellipsis' || (e.scrollWidth > e.clientWidth + 1 && getComputedStyle(e).overflow !== 'visible')) trunc.push((e.textContent || '').trim().slice(0, 30)); });
  out.truncated_text = trunc.slice(0, 8); out.n_truncated = trunc.length;
  out.minFontPx = minFont; out.tinyText = tiny.slice(0, 5);
  const sb = document.querySelector('[data-testid=stSidebar]'); out.sidebarVisible = !!sb && sb.getBoundingClientRect().width > 50;
  out.expandControl = !!document.querySelector('[data-testid=stExpandSidebarButton],[data-testid=stSidebarCollapsedControl]');
  return out; }"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--viewports", default="1920x1080,1440x900,1280x720,1024x768,768x1024,390x844")
    ap.add_argument("--forecasts", type=int, default=40)
    ap.add_argument("--max-capture-height", type=int, default=5000)
    ap.add_argument("--out", default="reports/phase6/visual_review.json")
    a = ap.parse_args()
    vps = [tuple(int(x) for x in v.split("x")) for v in a.viewports.split(",")]
    res = []
    with streamlit_server("tests/fixtures/fault_app.py", {"PARKISTEP_TEST_FAULT": ""}) as (url, log, proc), sync_playwright() as p:
        b = p.chromium.launch(); pg = b.new_page(viewport={"width": vps[0][0], "height": vps[0][1]})
        errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(f"{url}/replay-analysis", wait_until="networkidle"); pg.wait_for_selector("text=Start new replay", timeout=60000); time.sleep(2)
        btn(pg, "btn_start").click()
        wait_for(pg, lambda: (num(metrics(pg).get("Forecasts")) or 0) >= a.forecasts, 120)
        btn(pg, "btn_stop").click(); time.sleep(3)
        for (w, h) in vps:
            pg.set_viewport_size({"width": w, "height": h}); time.sleep(2)
            d = ROOT / "reports/phase6/screenshots/viewports" / f"{w}x{h}"; d.mkdir(parents=True, exist_ok=True)
            for title, _slug in PAGES:
                row = {"viewport": f"{w}x{h}", "page": title}
                try:
                    def sidebar_open():
                        bb = pg.locator('[data-testid=stSidebar]').first.bounding_box()
                        return bool(bb) and bb["x"] >= -1 and bb["width"] > 50
                    if not sidebar_open():                                                            # narrow: sidebar collapsed -> open it like a user
                        pg.locator('[data-testid=stExpandSidebarButton]').first.click(timeout=5000); time.sleep(1.2); row["opened_collapsed_sidebar"] = True
                    pg.locator('[data-testid=stSidebarNav] a', has_text=title).first.click(timeout=8000); time.sleep(3.5)
                    row["sidebar_open_after_nav"] = sidebar_open()
                    if w < 800 and sidebar_open():                                                    # overlay sidebar: close it so the page is captured as read
                        for sel in ('[data-testid=stSidebarCollapseButton] button', '[data-testid=stSidebarCollapseButton]', '[data-testid=stSidebarHeader] button', 'button[kind=headerNoPadding]'):
                            try:
                                pg.locator(sel).first.click(timeout=1500); time.sleep(1)
                            except Exception:                                                         # noqa: BLE001
                                continue
                            if not sidebar_open():
                                break
                        row["sidebar_closed_for_capture"] = not sidebar_open()
                    j = pg.evaluate(JS_CHECKS)
                    # Streamlit scrolls an inner container, so full_page=True captures only one viewport: grow the viewport to the content height
                    sh = pg.evaluate("() => { const m = document.querySelector('[data-testid=stMain]'); return m ? m.scrollHeight : document.documentElement.scrollHeight; }")
                    row["content_height_px"] = sh
                    tall = int(min(max(h, sh + 20), a.max_capture_height))
                    pg.set_viewport_size({"width": w, "height": tall}); time.sleep(2.0)
                    pg.screenshot(path=str(d / f"{title.lower().replace(' ', '_')}.png"))
                    pg.set_viewport_size({"width": w, "height": h}); time.sleep(0.8)
                    row["capture_truncated"] = sh + 20 > a.max_capture_height
                    row.update(j)
                    row["exception_elements"] = exceptions(pg)
                    row["horizontal_overflow"] = j["docScrollW"] > w + 2 or j["mainScrollW"] > j["mainClientW"] + 2
                    row["nav_reachable"] = True
                    row["passed"] = row["exception_elements"] == 0 and not row["horizontal_overflow"] and j["n_clipped"] == 0 and j["n_truncated"] == 0 and j["minFontPx"] >= 11
                except Exception as e:                                                               # noqa: BLE001
                    row.update({"error": f"{type(e).__name__}: {str(e)[:200]}", "nav_reachable": False, "passed": False})
                res.append(row)
                print(("PASS" if row["passed"] else "FAIL"), row["viewport"], title, {k: row.get(k) for k in ("horizontal_overflow", "n_clipped", "n_truncated", "minFontPx", "error") if row.get(k)})
        b.close()
    Path(a.out).write_text(json.dumps({"schema": "parkistep.phase6.visual_review/1", "viewports": [f"{w}x{h}" for w, h in vps], "pages": [t for t, _ in PAGES],
                                       "results": res, "automated_checks_all_passed": all(r["passed"] for r in res),
                                       "note": "automated geometry checks only; screenshots must also be inspected by a person"}, indent=2))
    return 0 if all(r["passed"] for r in res) else 1


if __name__ == "__main__":
    sys.exit(main())
