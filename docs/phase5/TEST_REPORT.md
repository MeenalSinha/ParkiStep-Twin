# Test report (actual execution, Python 3.12.3, 1 CPU)

Command: `PARKISTEP_REQUIRE_REAL=1 python -m pytest -q -p no:cacheprovider --junitxml=reports/phase5/junit_full_regression.xml`
Result: **324 collected, 324 passed, 0 failed, 0 skipped, 236.9 s.** `PARKISTEP_REQUIRE_REAL=1` turns "real data missing" into a failure instead of a skip.

| Suite | Tests | Data |
|---|---|---|
| Phase 1 (acquisition, config, ingestion, integration, quality, reporting, validation, regression) | 45 | synthetic fixtures + real |
| Phase 2 (alerts, config, events/windows, features, integration, models) | 64 | synthetic + real artifacts |
| Phase 3 (alerts, buffer, config, engine, persistence, replay, real data, real model) | 173 | synthetic fixtures for engine/buffer logic; real data/model in `real_*` |
| Phase 4 backend / real / app (AppTest) | 18 / 6 / 3 | backend: synthetic + test double; real: real FoG-STAR + real model; app: real Streamlit |
| **Phase 5 validation (new)** | 15 | config-failure tests use tmp config files; others real data + real model |

Phase 5 tests cover: missing/malformed/mistyped config; unknown override rejected; unavailable model (unsupported horizon, unknown model, missing artifact dir) reported as unavailable with no score or threshold; forecast integrity on a real replay (window end <= prediction time, probabilities finite in [0,1], monotonic times, unique ids, single threshold equal to the model's, horizon equals the model's, 0 future-data violations); warnings tied to real predictions; reproducibility across repeated runs and across pause/resume slicing; paused session is a no-op; double start creates no second twin; wrong patient/recording refused; source data and model files are byte-identical after a replay; no embedded recording/model IDs or absolute paths in dashboard sources.

Replays inside pytest are capped by `replay.max_observations` (1,500 in the Phase 5 file) for speed; the full recording was run in the browser and by `scripts/measure_phase5.py`.

Browser checks (not pytest): `scripts/browser_pages_phase5.py` and `scripts/browser_replay_phase5.py` (Playwright/Chromium).
