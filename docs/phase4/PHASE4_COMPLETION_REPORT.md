# Phase 4 completion report - clinician dashboard

> Research prototype; scores are not diagnoses. **Three different things are kept apart: (a) a dashboard that launches, (b) a dashboard integrated with the
> digital-twin engine, (c) a clinically effective model. This report claims (b) at the service level only, has NOT demonstrated (a) in a browser, and does not claim (c).**

## 1. Phase 3 verification
`PASS_WITH_NON_BLOCKING_WARNINGS` (`PHASE3_VERIFICATION.md`): real Phase 1 audit re-run (PASS_WITH_WARNINGS, 10 warnings), 24/24 checksums, real inference, real replay reproducing digest `13ee0b18...`. The 282 historical pytest results could not be re-run (pytest unavailable).

## 2. Issues found / repairs
No defect in Phases 1-3 was found; none edited. Environment gaps: no pytest, pyarrow, streamlit, plotly, no network.

## 3. Files
Created: `configs/phase4.yaml`; `src/parkistep/dashboard/{__init__,config,replay_session,service,performance,plotdata,analysis}.py`; `app/{Home,bootstrap,theme,state}.py`, `app/components/{common,prediction_cards,sensor_charts,warning_timeline,replay_controls,performance_tables}.py`, `app/views/{overview,patient_profile,movement_monitor,early_warning,warning_center,replay_analysis,model_performance}.py`; `scripts/smoke_dashboard_stub.py`; `tests/test_phase4_{backend,real,app}.py`; `docs/phase4/*`. Modified: `README.md` (Phase 4 section), `requirements.txt`, `pyproject.toml` (dashboard extra). Phase 1-3 sources: unchanged.

## 4-5. Integration with the twin service
Incremental `ReplaySession` over `engine.ingest`; `DashboardService` uses `build_engine`, `TwinService.view`, `select_recordings`, `build_replay_stream`, the model gateway, `ReplayRunner.digest/compare`, `engine.acknowledge_warning`. No second prediction/alert logic.

## 6. Real recording replay (actual runs, real model, in-sample)
Default selection chosen by the Phase 3 policy: participant 2, recording `p2-s1-t4`. Stepped in 38 slices incl. a pause/resume: 11,176 offered/processed, 0 rejected, 922 forecasts, 14 warnings, 0 future-data violations, **digest identical to the blocking CLI replay**, retrospective 1/7 events detected (identical). Fusion model (`fusion/random_forest/h3`), 2,500 observations: 199 forecasts, `clinical_used=true`, threshold 0.6212, 0 violations.

## 7. Prediction/warning display verification
Verified at the service level (tests) and by stub-rendering every view in five replay states with the real service: no forecast -> state "none" (not zero risk); ground truth withheld until the session ends; model-unavailable and invalid selections raise explicit errors and create no twin. **Visual correctness in a browser is unverified.**

## 8. Model-performance reporting
Page reads `reports/phase2/phase2_metrics.json`; a test compares every displayed row with the file. Default model (sensor_only RF, 3 s): event sensitivity 0.21 (16/75 evaluable events), 74.3 false alerts/h over 0.90 monitored h, median lead 1.65 s - weak; shown as is, with CIs, folds, calibration caveat.

## 9. Tests actually run
| Suite | Result | Runner |
|---|---|---|
| `tests/test_phase4_backend.py` (synthetic + test double) | 18 passed, 0 failed | **stand-in function runner, not pytest** |
| `tests/test_phase4_real.py` (real data + artifacts, CSV tables) | 6 passed, 0 failed | stand-in runner, scratch copy |
| `scripts/smoke_dashboard_stub.py` (views vs fake streamlit/plotly) | 5 states x 7 views, 0 failures | stub - checks Python errors only |
| `tests/test_phase4_app.py` (Streamlit AppTest, 3 tests) | **NOT RUN** (streamlit missing) | - |
| Phase 1-3 suites (282 historical) | **NOT RE-RUN** (pytest missing) | - |
Nothing was skipped by weakening; the unrun items are unrun. Please run `PARKISTEP_REQUIRE_REAL=1 python -m pytest` after `pip install -r requirements.txt`.

## 10. Launch
`streamlit run app/Home.py`

## 11. Known limitations
1. The Streamlit/Plotly code was never executed under real Streamlit; API-usage or layout errors are possible (`st.fragment(run_every)`, `st.navigation`, plot subplots). Expect to fix small issues on first launch.
2. Phase 1-3 pytest suites unverified now; parquet needs pyarrow.
3. Weak model; uncalibrated; replays in-sample; ~20 ms/forecast limits speed; single-process, no live wearable adapter; replay scope is one recording.
4. Warning-timing categories are descriptive; official counts come from Phase 3/Phase 2 rules. Hard-coding of identifiers was avoided but no automated static guard was added for Phase 4.

## 12. Acceptance gates
| Gate | Status |
|---|---|
| 1 Previous phases verified | **Met with warning** (runtime verified; pytest not re-run) |
| 2 Patient context | Met at service level; UI unrun |
| 3 Movement monitoring | Implemented; plot data tested; **rendering unverified** |
| 4 Engine integration | **Met** (real model, sequential replay, digest-identical) |
| 5 Prediction integrity | **Met** (service/tests); UI wording unverified in browser |
| 6 Replay workflow | Met at session level; **buttons/fragment loop unverified in a browser** |
| 7 Performance transparency | Implemented; numbers tested against file; rendering unverified |
| 8 Testing and documentation | **Partially met** - see section 9 |
