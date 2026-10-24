"""SYNTHETIC fixtures for Phase 2 tests (fake data; never touches data/raw)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]


def rec_frame(rid, pid, n, dt, t0, fog_runs=(), sid=1, task=1):
    ts = t0 + np.arange(n) * dt
    fog = np.zeros(n, int)
    for a, b in fog_runs:
        fog[a:b] = 1
    return pd.DataFrame({"recording_id": rid, "participant_id": pid, "session_id": sid, "task_id": task, "timestamp_s": ts,
                         "fog": fog, "fog_severity": fog * 2, "activity": 1})


def shared_clock_sensor(dt=0.1):
    """Three recordings on ONE running clock (as in the real data): r1 and r2 of participant 1 abut, r3 is participant 2."""
    a = rec_frame("r1", 1, 100, dt, 0.0, [(30, 40), (60, 65)])
    b = rec_frame("r2", 1, 100, dt, a.timestamp_s.iloc[-1] + dt, [(50, 58)], task=2)
    c = rec_frame("r3", 2, 80, dt, b.timestamp_s.iloc[-1] + dt, [])
    return pd.concat([a, b, c], ignore_index=True)


COLS = dict(recording="recording_id", participant="participant_id", session="session_id", task="task_id",
            time="timestamp_s", label="fog", positive=1)
WIN = dict(input_window_s=1.0, stride_s=0.2, timestamp_tolerance_fraction=0.05, exclude_input_overlapping_event=True,
           min_time_since_previous_event_s=0.0, require_full_future=True)


def synthetic_real_schema(n_participants=12, recs=2, seconds=40, hz=30.0, seed=0):
    """Synthetic data with the real FoG-STAR canonical schema; amplitude rises before each onset so it is learnable."""
    rng = np.random.default_rng(seed)
    pos, sigs = ["ankleL", "ankleR", "back", "wrist"], ["acc", "gyro"]
    chans = [f"{p}_{s}_{a}" for p in pos for s in sigs for a in "xyz"]
    dt = 1.0 / hz
    frames, t0 = [], 0.0
    for pid in range(1, n_participants + 1):
        for r in range(recs):
            n = int(seconds * hz)
            fog = np.zeros(n, int)
            if pid % 4 != 0:                                   # every 4th participant has no events
                for onset in (int(n * 0.35), int(n * 0.75)):
                    fog[onset:onset + int(2 * hz)] = 1
            amp = np.ones(n)
            for o in np.where(np.diff(np.r_[0, fog]) == 1)[0]:
                amp[max(0, o - int(3 * hz)):o] = 3.0           # pre-onset movement change
            d = {"recording_id": f"p{pid}-s1-t{r + 1}", "participant_id": pid, "session_id": 1, "task_id": r + 1,
                 "timestamp_s": t0 + np.arange(n) * dt, "fog": fog, "fog_severity": fog * 2, "activity": 1}
            for c in chans:
                d[c] = rng.normal(size=n) * amp
            frames.append(pd.DataFrame(d)); t0 += n * dt
    sensor = pd.concat(frames, ignore_index=True)
    clin = pd.DataFrame({"participant_id": range(1, n_participants + 1), "age": rng.integers(55, 85, n_participants),
                         "gender": ["M", "F"] * (n_participants // 2), "disease_duration": rng.integers(1, 20, n_participants).astype(float),
                         "hoehn_yahr": 2.5, "updrs_iii": rng.integers(10, 60, n_participants).astype(float),
                         "fog_q": rng.integers(0, 24, n_participants).astype(float), "moca": rng.integers(15, 30, n_participants).astype(float),
                         "fes_i": 30.0, "pdq_8": rng.integers(0, 32, n_participants).astype(float)})
    clin.loc[2, "moca"] = np.nan
    return sensor, clin
