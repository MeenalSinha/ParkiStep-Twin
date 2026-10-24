# End-to-end validation (real FoG-STAR recording, real trained model, real Streamlit server, real Chromium)

Recording: participant 2, `p2-s1-t4` (chosen by the Phase 3 selection policy, not by me). Model: `sensor_only/random_forest`, 3 s horizon, threshold 0.703 from the model manifest.
Browser run (`scripts/browser_replay_phase5.py`), values read from the page:

| Step | Observed |
|---|---|
| Start at 5x | 450 of 11,176 offered, 28 forecasts, 0 warnings |
| Pause | 900 offered, 65 forecasts, 1 warning; **identical after two further waits (3 s and 7 s)** |
| Resume | continued from 900 to 1,500, 115 forecasts, 2 warnings, no duplicates |
| Navigate to Movement Monitor mid-replay and back | no exception; replay continued |
| Completion | 11,176 processed / 0 rejected, 922 forecasts, 14 warnings, 0 future-data violations, digest `13ee0b18...` |
| Retrospective view | appeared only after completion; labelled in-sample |

The digest equals the Phase 3 saved digest and the digest from three independent runs in `measure_phase5.py`.

**Not verified:** the Stop button, a failed replay, and a stale-prediction display in the browser; retrospective metrics were not independently recomputed (only the digest and counts were compared); only one recording was replayed in the browser. These paths are covered at service level by the earlier Phase 3/4 tests, not by a browser test.
This shows the software pipeline works end to end. It is not evidence of clinical forecasting performance: the replay is in-sample for the saved model.
