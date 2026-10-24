# Phase 4 implementation guide - clinician dashboard

> Research prototype. Scores are experimental model estimates, not diagnoses; not a medical device.

## Architecture
```
app/ (Streamlit, presentation only)            src/parkistep/dashboard/ (pure Python)        src/parkistep/twin|forecast (Phases 1-3, unchanged)
Home.py -> st.navigation(views from config) -> DashboardService  -----------------------> TwinEngine / TwinService / ModelHandle / ForecastPredictor
views/*.py  components/*.py  theme.py          ReplaySession (incremental replay)           select_recordings, build_replay_stream, ReplayRunner.digest/compare
bootstrap.py state.py                          PerformanceReports (saved Phase 2 metrics)   Phase1ClinicalSource
```
The UI contains no feature extraction, inference, thresholds, warning rules, buffer logic, label construction or patient matching.
The only Phase 3 file additions are new modules; **no Phase 1-3 source file was edited**.

## Data flow
1. `DashboardService` loads the Phase 1 tables, clinical source, model manifests/handles (SHA-256 verified by the Phase 3 gateway) and `phase2_metrics.json` once (cached base service); each browser session gets a `fork()` that shares those read-only objects but owns its replay sessions.
2. *Start* -> `create_session` validates patient/recording ownership with Phase 3 `select_recordings`, loads the model (no fallback), builds a **new engine** (own twin) and a `ReplaySession`.
3. While RUNNING, each UI refresh calls `advance(sim_seconds, max_observations)`; records go to `engine.ingest` strictly in order, once. The views then read `TwinService.view(...)`, `engine.recent_predictions`, `engine.warning_history`.
4. On completion the engine session is ended, a summary is built with Phase 3 `ReplayRunner.digest`, and only then `retrospective()` releases the ground-truth comparison (`ReplayRunner.compare`, Phase 2 scoring rules).

## Replay controls
Start / Pause / Resume / Stop, patient, recording, model and speed selection. Speed = simulated seconds per wall second; slice size = `speed x refresh_interval_s`, capped by `max_observations_per_tick`; "as fast as possible" is bounded by the cap. The achieved speed can be lower because inference takes about 20 ms per forecast.
Safety: starting an unstarted session twice returns the same session; a rerun after completion offers nothing; a running/paused replay cannot be replaced silently; a failed start creates no twin; failures set status FAILED with the error kept (log + UI), never a substitute recording. Persistence is off by default (`replay.persist_state`); when on, the Phase 3 overwrite protection applies and surfaces as a message.

## Pages
Overview, Patient Profile, Movement Monitor, Early Warning, Warning Center, Replay Analysis (live + retrospective), Model Performance. Order/titles: `phase4.navigation`.
Live pages are wrapped by `components.common.live_page`: a Streamlit fragment that, only while a replay is RUNNING, re-runs at `refresh_interval_s` and advances the replay first. There is no browser-side timer and no simulated arrival.

## Interpretation rules implemented
* No forecast / stale / unavailable / historical are distinct states with their own labels; none is rendered as low risk. A not-current forecast is shown only as "last computed (not current)".
* Scores are described as uncalibrated (`calibration_status` never reports validated calibration).
* Sensor-only models: clinical record shown as context with an explicit note; fusion: fields used come from the model manifest.
* Missing clinical values display "not recorded" and are never imputed or shown as negative findings.
* Ground truth is shown only in the retrospective layer after the session ended; warnings after onset are labelled as not early warnings.
* In-sample replay results are labelled separately from the participant-grouped Phase 2 results on the Model Performance page.

## Configuration (`configs/phase4.yaml`)
Title/layout, navigation, default selection policies (delegated to Phase 3 policies), replay speeds/refresh/caps, plot limits, table limits, display precision, theme colours, status labels/colours/help, fixed explanatory text, feature flags. Override without editing files: `PARKISTEP_PHASE4_SET="replay.default_speed=10;plots.history_s=30"`, `PARKISTEP_PHASE3_SET=...`, `PARKISTEP_PHASE4_CONFIG=path`, `PARKISTEP_PROJECT_ROOT=path`. Model values come from the manifest, dataset fields from `configs/datasets.yaml`, warning policy from Phase 3, metrics from the Phase 2 reports.

## Error handling
Invalid config -> actionable error screen; dataset unavailable -> explanation + next step; model artifact unavailable -> explicit message (no substitute); no replay -> "empty is not low risk"; replay failed -> status + message.

## Known limitations
See `PHASE4_COMPLETION_REPORT.md` section 8. Most important: the Streamlit UI was **not executed in a browser** in the build environment.
