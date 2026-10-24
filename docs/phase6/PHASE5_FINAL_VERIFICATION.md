# Phase 5 final verification (performed at the start of Phase 6)

**Exit decision: `PASS_WITH_NON_BLOCKING_WARNINGS`.**
Every critical Phase 5 item on the backlog was executed; the five defects it uncovered were repaired and re-tested. The warnings that
remain do not invalidate the release but must be closed before public publication (section 14). Historical Phase 5 reports are
unchanged; this document records the *new* validation run and how it differs from the earlier one.

Evidence files are in `reports/phase6/` (named below). The machine-readable summary is `docs/phase6/FINAL_RELEASE_MANIFEST.json`.
Test counts are quoted from the generated JUnit files, not remembered.

## 1. What changed relative to the Phase 5 report

| | Phase 5 report | Phase 6 re-run |
|---|---|---|
| Full regression | 324 passed | **baseline 324 passed** on the untouched upload (`junit_full_regression_initial.xml`), then **331 passed** after repairs + 7 new tests (`junit_full_regression_final.xml`), **331 passed** again in a fresh virtual environment (`junit_clean_environment.xml`). 0 failed, 0 skipped, `PARKISTEP_REQUIRE_REAL=1`. |
| Tests removed or weakened | - | None. `tests/` differs from the upload only by additions (`test_phase6_repairs.py`, `fixtures/fault_app.py`); configs are byte-identical (`source_changes_phase6.diff`). |
| Defects | none open | five found and repaired (section 2) |

## 2. Defects found and repaired (all with regression tests where code-level)

| # | Defect (how found) | Repair | Test |
|---|---|---|---|
| D1 | After a persistence failure the failed replay left its SQLite transaction open; the next replay died with a raw `OperationalError: database is locked` that the UI does not catch (service probe, then browser) | `StateStore.abort()`, `TwinEngine.abort_store()`, release in `ReplaySession._fail`, sqlite errors mapped to `StateStoreError` | `test_storage_failure_fails_replay_visibly_and_releases_the_database`, `test_sqlite_error_when_opening_the_store_becomes_state_store_error` (both fail on the old code) |
| D2 | One observation with an unusable timestamp crashed the whole replay (`TypeError` in pacing arithmetic) instead of being rejected and counted (browser data-fault scenario) | pacing uses only finite numeric timestamps; the record is still offered so the engine rejects and counts it | `test_unusable_timestamp_is_rejected_and_counted_not_fatal[not-a-timestamp]` and `[None]` fail on old code; `[nan]`, `[inf]` pass on both (they never crashed) |
| D3 | A completed *persisted* session could keep an uncommitted write (`save_state` after the final flush), blocking the next persisted replay with `database is locked` (soak test, cycle ~47) | `flush()` after `save_state` in `ReplaySession._finish` | `test_every_persisted_session_ends_with_no_open_write_transaction` (fails on old code; covers every patient) |
| D4 | A recording that yields no forecasts (e.g. `p22-s1-t4`, 277 windows rejected as `insufficient_sensor_quality`) never said why anywhere in the UI | Replay Analysis lists the unavailable-forecast reasons | multi-patient browser check `recording with no forecasts: the reason ... is visible` |
| D5 | Display bugs found by visual review: metric values truncated with ellipses at 768 px ("p2-...", "WAI..."); "Recording None / 0 accepted" after a stopped replay; Patient Profile table logged a pyarrow conversion traceback every render (mixed number/text column); a table column called "probability" next to a note saying the score is not a probability | theme CSS wraps/scales metric text; fall back to the session's recording id and processed count; `Value` column formatted as text; column renamed "score (uncalibrated)" | visual review (42/42), screenshots re-inspected after the fix |

A sixth finding is a reproducibility risk, not a code defect: a fresh `pip install -r requirements.txt` resolves scikit-learn 1.9.1,
which loads the 1.8.0-trained models with `InconsistentVersionWarning`. Mitigation: `requirements-lock.txt` (verified: fresh venv,
0 warnings, 24/24 artifacts) and a `software_mismatch` field in `p6_verify_artifacts.py`.

One error was made and corrected *during* verification: the first version of `p6_audit_recheck.py` coerced the text column
`gender` to numbers and reported it missing for all 22 participants. The script now uses raw missingness; the corrected result (4
clinical fields with exactly one missing participant each) agrees with the Phase 1 report. A slide that had used the wrong count
was rebuilt.

## 3. Checklist 1 - repository integrity
Structure, documentation of Phases 0-5, configs, modules, manifests, generated outputs, tests and dependency files were all
inspected. **The uploaded archive contains no `.git` directory**, so there is no working tree or history to inspect and no
uncommitted work to protect; the pre-edit state was preserved as a diff (`source_changes_phase6.diff`, 10 source files). Release vs
local files: see `RELEASE_CHECKLIST.md`.

## 4. Checklist 2 - full regression
Real-data and genuine-model tests executed (0 skipped under `PARKISTEP_REQUIRE_REAL=1`); Streamlit AppTest tests executed. Counts above.

## 5. Checklist 3 - Phase 1 audit warnings (independent re-verification)
`scripts/p6_audit_recheck.py` re-derives each fact from the raw CSVs with pandas only (`phase1_warning_recheck.json`); the audit was
also regenerated into a temporary directory (18 s) and matches the saved report except environment strings. Status unchanged:
`PASS_WITH_WARNINGS`, 10 issues.

| Warning (code) | Exact meaning / independent re-derivation | Affects validity of forecasting/evaluation? | Blocking? | Action / result |
|---|---|---|---|---|
| CLINICAL_MISSING_VALUES x4 | one participant each is missing disease_duration (p22), hoehn_yahr (p12), moca (p11), fes_i (p21) - re-derived exactly | only the fusion experiments; sensor-only models never read clinical data | No | kept as missing (no deletion); fusion models impute inside the fitted pipeline; dashboard shows "not recorded"; documented |
| CLINICAL_OUT_OF_RANGE x2 | fog_q: 3 of 22 values 25-27 vs documented 0-24; fes_i: 9 of 21 values 11-100 vs documented 16-64 - re-derived exactly | fusion only; possible scoring-scale ambiguity in the source | No | flagged, not altered (rule not loosened); recommend checking the dataset authors' scale definitions |
| SENSOR_MISSING_VALUES | 15.25 % of 329,027 rows have a missing channel (reported 15.3 %); 14 recordings lack an entire sensor position | yes, mitigated: windows below the quality threshold produce no forecast | No | reported as `insufficient_sensor_quality`; UI states this (D4); no imputation or deletion |
| RECORDING_BOUNDARIES_INVISIBLE_IN_TIMESTAMPS | one running clock; 105 of 105 recording boundaries exactly one step apart; recordings contiguous in file | would matter if windows crossed recordings | No | windows/events are built per recording id; guarded by `test_boundary_between_abutting_recordings_is_respected` and `test_recording_boundary_on_real_data_never_mixes_recordings` (pass) |
| ANNOTATION_UNDOCUMENTED_VALUE | `activity` contains value 0 in 5,197 rows (1.58 %) - re-derived | none (activity is not a model input or label) | No | kept unchanged |
| JOIN_MISSING_CLINICAL_AFTER | clinical gaps propagate to joined rows (disease_duration 6,249; hoehn_yahr 17,287; moca 13,855; fes_i 10,992) | fusion only | No | expected consequence of the four missing values; participant matching itself is complete (22 of 22) |

Also re-derived and unchanged: 22 participants, 106 recordings, 101 annotated episodes in 16 participants (9 run to the end of a
recording, 6 contain more than one severity value). Nothing was suppressed or relaxed.

## 6. Checklist 4 - model artifacts
`scripts/p6_verify_artifacts.py` discovered **24 manifests** (not assumed) and found all 24 pass: file present, SHA-256 recomputed
(algorithm sha256, 64 hex) and equal to the manifest, experiment id equals its directory, horizon and identity match the file
name, threshold finite (value and policy recorded; the logistic models' thresholds are very high, 0.998-0.9999, so they almost never
alert, whereas the random-forest thresholds are 0.48-0.71), feature schema non-empty, training scope recorded, loaded through the genuine `ForecastPredictor`, one real window
scored with a valid, horizon-/threshold-consistent output. **Negative control:** one flipped byte in a copy was detected (23/24).
`verify_twin_model.py` also passes. Evidence: `artifact_verification.json`, `verify_twin_model.log`.

## 7. Checklist 5 - browser failure paths (real Chromium, real Streamlit, real data/model)
All scenarios pass (`browser_failure_paths.json`; faults injected only by `tests/fixtures/fault_app.py`, never by editing data or models):
* **Stop (15 checks):** counters freeze; state shows *Stopped*, not *Completed*; twin ends in the ended-session state; history and warnings remain accessible; last forecast labelled *not current*; no duplicate twin (server log shows exactly one creation); a browser reload is a new session and does not restart the replay.
* **Failed replay (10):** stream failure mid-replay -> clear error naming the cause, "no synthetic data were substituted", no new forecasts, no retrospective analysis, error logged with traceback; a new valid replay then runs.
* **Stale forecast (9):** sensor stream stalls -> *No current forecast*, last score shown as *not current*, never zero, twin STALE; sensor timestamp and forecast cards stay distinguishable; fresh forecast returns when data resume.
* **Persistence failure (7):** storage fails mid-replay -> failure shown, counters frozen, database holds no more predictions than were produced (uncommitted writes rolled back), a replay for another patient then starts (D1).
* **Data integrity (5):** injected duplicate, invalid-timestamp, out-of-order and missing-channel records -> replay completes (D2), rejections counted and shown, forecasting continues.
* **Model inference failure (5):** no new forecasts, no zero score, recovery when the model works again.
* **Bad inputs (17):** malformed, metric-less and missing evaluation reports each give a specific message ("could not be read: ...", "no results mapping", "file not found ... run train_phase2.py") with no invented figures; a corrupted persisted twin makes the replay refuse to start with an explicit "never overwritten" message and the corrupt record is left untouched. (This scenario was run separately on identical code and merged into the file.)

## 8. Checklist 6 - dashboard-level fault injection
| Case | Where verified |
|---|---|
| missing required sensor field; invalid timestamp; duplicate; out-of-order | browser `data_integrity_faults` plus a service-level probe of the same four faults: the duplicate (`DUPLICATE_TIMESTAMP`), invalid-timestamp (`INVALID_TIMESTAMP`) and out-of-order (`OUT_OF_ORDER`) records were each rejected and counted; the record **missing one sensor channel was accepted** (1,499 of 1,502 offered were processed) and is handled as a missing value by the sensor-quality gate - by design of the Phase 3 validator, not rejected |
| missing clinical values | Phase 1/4 mechanisms + `Patient Profile` "not recorded"; not separately re-exercised in a browser for a patient with a missing value |
| mismatched patient/recording | existing `test_phase4_real` (`SelectionError: ... belongs to participant`) - passes; the UI only offers valid combinations |
| model inference failure; stale replay | browser scenarios above |
| unavailable twin / no replay yet | `test_no_replay_state_is_not_low_risk` (passes) |
| malformed report; missing metrics | browser `bad_inputs` |
| invalid saved state | engine-level `test_corrupt_or_foreign_state_is_refused...` (passes) and browser `bad_inputs` (dashboard refuses, never loads it) |

Validation logic was **not** duplicated in the dashboard; the scenarios exercise the Phase 1/3 validators.

## 9. Checklist 7 - independent recomputation of retrospective metrics
`scripts/p6_recompute_metrics.py` (does not import the project's event, alert or replay code): raw-CSV annotations, saved predictions
and warnings, manifest horizon/window/stride/policy. Identity checks pass (same participant and recording everywhere). **13 of 13
metrics match** the saved analysis, including: 7 annotated events (all with forecast coverage), 1 detected (sensitivity 0.143),
1 valid early warning, 3 false warnings, 10 warnings excluded because they fell inside an annotated episode, 0 excluded as
censored, 0.0227 scored hours, 132.0 false warnings/h on this single short recording, median lead 2.40 s. Warnings re-derived from
the saved scores with the threshold and merge gap alone reproduce the engine's warning times. Alerts at or after onset are
excluded and never credited. The browser replay on the final code shows the same values (`browser_full_replay.json`).

## 10. Checklist 8 - multiple patients and recordings
`browser_multi_patient.json`: patients and recordings are **discovered** (default, fewest-recordings, most-recordings, median
patients; first and last recording of each): 3 patients, 6 recordings, one browser session, plus a non-default model. **47 of 47
checks pass**: recording belongs to the selected patient, correct clinical summary on Patient Profile, counters belong to the new
twin, selector follows the replay, non-default model shown and used, a double-click on Start created no extra twin, no
exceptions. A recording with zero forecasts is handled honestly (D4). Warning-history isolation between patients is also guaranteed
by one engine per session (not separately asserted at the engine level).

## 11. Checklist 9 - memory and resource reliability
`scripts/p6_soak.py` (configurable cycles, observations, warm-up, limits), service level, real data/model: **94 cycles in 244 s**
(24 complete, 24 stop-midway, 24 injected-failure, 22 persisted), cycling through the real patients. Memory 502 -> 555 MB, mostly in
the first cycles; fitted slope after warm-up 0.32 MB/cycle (limit 2.0); threads constant at 6; file descriptors <= 10; open SQLite
handles <= 6 = the configured retention of finished sessions plus one (not unbounded); live sessions <= 12 = twice the retention
cap; failed/stopped/completed cycles ended in the expected states. **Limits:** one process, ~4 minutes, a few hundred thousand
observations in total; this bounds growth over that run and does not prove the absence of a slow leak over hours or days. Browser-level
resource sampling of the Streamlit server was not performed. Duplicate-timer/thread checks: thread count did not grow.

## 12. Checklist 10 - visual and responsive review
`scripts/p6_visual_review.py`: 7 pages x 6 viewports (1920x1080, 1440x900, 1280x720, 1024x768, 768x1024, 390x844) from one live replay
session; **42 of 42** page-viewport automated checks pass on the final code (no exception, no horizontal overflow, nothing clipped
beyond the viewport, no truncated metric text, minimum text size 12.8 px; the sidebar is reachable through its expand control at
768 and 390). Screenshots are captured at full content height (the first version captured one screen only - fixed).
**Hand-reviewed screenshots (7):** Overview 1440x900; Early Warning 1440x900 and 390x844; Movement Monitor 1440x900; Replay
Analysis 768x1024 (before and after the fix); Warning Center 390x844; Model Performance 1280x720. The other 35 combinations were
checked only by the automated geometry rules. Observations: layouts stack cleanly on narrow screens, wide tables scroll inside
their own container, warning states are visually distinct; defects found are listed under D5. Not assessed: colour-contrast
measurement, keyboard navigation, screen readers.

## 13. Checklist 11 - secrets, licence, repository hygiene
`scripts/p6_repo_audit.py` (`repo_audit.json`): 321 files scanned; **0 secret-pattern matches, 0 environment/key files, 0 database
files**. The three files above 5 MB (raw sensor CSV, two processed parquet tables) are git-ignored; no un-ignored file above 1 MB
other than documents/images. 13 mentions of absolute local paths exist in *generated* reports (recorded provenance of earlier runs;
no source or config requires them). Dependency licences as declared by the packages are in `THIRD_PARTY_NOTICES.md`. A `LICENSE`
(MIT) was added with copyright holder and year as **pending placeholders**. Not possible here: scanning Git history (none was
supplied) - run a history scanner (e.g. gitleaks) on the real repository before publishing; if anything is found it must be
rotated and removed from history, which deleting it from the working tree does not do. Dataset redistribution: none; trained models
are derivatives of CC BY 4.0 data and their redistribution terms need the owner's confirmation.

## 14. Checklist 12 - README and documentation accuracy
The README was rewritten around the 16 required sections with every performance figure traced to `reports/phase2/` (the manifest
script verifies that the quoted default-model numbers occur in the README). Command status:

| Command | Status in Phase 6 |
|---|---|
| `pip install -r requirements-lock.txt` / `requirements.txt` | executed in two fresh virtual environments (both succeed) |
| `python scripts/audit_data.py` | executed (18 s, PASS_WITH_WARNINGS, matches saved report) |
| `python scripts/verify_twin_model.py`, `scripts/p6_verify_artifacts.py` | executed (also in the fresh environment) |
| `streamlit run app/Home.py` | executed (health endpoint answered in the fresh environment; used by all browser runs) |
| `PARKISTEP_REQUIRE_REAL=1 python -m pytest ...` | executed (331 passed, also in the fresh environment) |
| `scripts/replay_twin.py`, `create_twin.py`, `inspect_twin.py` | help text verified; executed in earlier phases; not re-executed in Phase 6 (the dashboard replay path was) |
| `scripts/prepare_data.py`, `prepare_phase2_windows.py`, `train_phase2.py`, `evaluate_phase2.py` | help text verified; **not re-executed** in Phase 6 (training is long; saved artifacts verified instead) |
| `scripts/acquire_data.py --download` | **unverified**: zenodo.org was unreachable from the verification environment |

## 15. Remaining non-blocking warnings (must be resolved before publication)
1. Licence copyright holder/year are placeholders; the licence choice needs the owner's confirmation.
2. Git history cannot be scanned (not supplied); the repository has not been published.
3. Redistribution terms of the trained models (derived from CC BY 4.0 data) are unconfirmed; the dataset licence statement should be re-read on the Zenodo record.
4. The `--download` path and the Phase 2 re-training path were not re-executed.
5. The soak test is short; no browser-level resource sampling.
6. 35 of 42 viewport screenshots were not looked at by a person; accessibility not assessed.
7. fog_q/fes_i out-of-range values should be checked against the dataset authors' scale definitions.

## 16. Decision
No critical Phase 5 item remains open. The classification is `PASS_WITH_NON_BLOCKING_WARNINGS`; Phase 6 proceeded on that basis.
