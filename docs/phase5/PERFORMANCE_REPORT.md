# Performance report (measured by `scripts/measure_phase5.py --repetitions 3 --max-observations 0`)

Machine: Linux 6.18, **1 CPU, 3.9 GB RAM**, Python 3.12.3, pandas 3.0.2, numpy 2.4.4. Raw output: `reports/phase5/performance_measurements.json`.
Recording: Phase 3 default selection (participant 2, `p2-s1-t4`, 186.25 s), full recording, real model (sensor-only random forest, 3 s horizon).

| Measure | Value |
|---|---|
| Config load | 0.012 s |
| Service build | 0.040 s |
| Warm-up (dataset discovery + model load) | 2.44 s |
| Twin initialisation | 0.37 / 0.41 / 0.38 s |
| Full replay wall time (11,176 observations, 922 forecasts) | 27.0 / 27.5 / 28.6 s |
| Ingestion incl. inference, per observation | 2.42 / 2.46 / 2.56 ms |
| Model inference, mean (max) | 21.4-22.6 ms (41-78 ms) |
| Longest single UI tick at max speed | 1.6-2.0 s |
| RSS: process start / after load / end | 108 / 447 / 350 MB |
| Digest across 3 repetitions | identical |

Interpretation, with limits: 186 s of recording replays in about 27 s on this machine (about 7x real time). Memory grew about 13 MB over three replays after load; three repetitions cannot establish or rule out a slow leak, so I do **not** claim "no leak". I did not measure application startup under a browser, per-rerun dashboard refresh time, or long-run behaviour. No acceptance targets were invented; these are baselines.
