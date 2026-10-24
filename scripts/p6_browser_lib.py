"""Shared helpers for the Phase 6 real-browser checks (Playwright + Chromium + a real Streamlit server)."""
from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def streamlit_server(entry: str = "app/Home.py", env: dict | None = None, port: int | None = None, log_path: Path | None = None):
    port = port or free_port()
    e = {**os.environ, **{k: str(v) for k, v in (env or {}).items()}}
    log_path = Path(log_path or f"/tmp/parkistep_server_{port}.log")
    log = open(log_path, "w")
    proc = subprocess.Popen([sys.executable, "-m", "streamlit", "run", entry, "--server.headless", "true", "--server.port", str(port),
                             "--browser.gatherUsageStats", "false", "--server.fileWatcherType", "none"],
                            cwd=ROOT, env=e, stdout=log, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}"
    try:
        import urllib.request
        for _ in range(120):
            try:
                if urllib.request.urlopen(url + "/_stcore/health", timeout=2).read().strip() == b"ok":
                    break
            except Exception:                                            # noqa: BLE001
                time.sleep(1)
        else:
            raise RuntimeError(f"streamlit did not become healthy; see {log_path}")
        yield url, log_path, proc
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()


def metrics(pg) -> dict:
    """label -> value of every st.metric currently on the page."""
    out = {}
    for m in pg.locator('[data-testid="stMetric"]').all():
        try:
            label = m.locator('[data-testid="stMetricLabel"]').inner_text().strip()
            out[label] = m.locator('[data-testid="stMetricValue"]').inner_text().strip()
        except Exception:                                                # noqa: BLE001
            pass
    return out


def num(x) -> int | None:
    m = re.search(r"-?[\d,]+", str(x or ""))
    return int(m.group(0).replace(",", "")) if m else None


def exceptions(pg) -> int:
    return pg.locator('[data-testid="stException"]').count()


def body(pg) -> str:
    return re.sub(r"[ \t]+", " ", pg.inner_text("body"))


def goto_page(pg, base: str, slug: str, ready: str | None = None, timeout=90000):
    pg.goto(f"{base}/{slug}", wait_until="networkidle", timeout=timeout)
    if ready:
        pg.wait_for_selector(f"text={ready}", timeout=60000)
    time.sleep(1.5)


def nav(pg, title: str):
    pg.locator('[data-testid="stSidebarNav"] a', has_text=title).first.click()
    time.sleep(3)


def wait_for(pg, pred, timeout_s=90, every=1.0):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        if pred():
            return True
        time.sleep(every)
    return False


def badge_text(pg) -> str:
    return body(pg)
