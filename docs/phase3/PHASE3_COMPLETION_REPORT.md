# Phase 3 completion report - digital-twin engine

> Research prototype. Outputs are model scores, not diagnoses. **Unit-test success, successful system integration and clinically
> meaningful predictive performance are three different things; this report claims the first two and explicitly does not claim the third.**

## 1. Previous-phase verification (details: `PHASE2_VERIFICATION.md`)
Exit status **`PASS_WITH_NON_BLOCKING_WARNINGS`**. 109 pre-existing tests pass (0 skipped); the real-data audit was reproduced and the key
dataset facts re-derived with independent code; 24/24 model manifests have their model file and matching SHA-256; inference on raw
trailing windows equals the training feature path exactly (180 real predictions, max difference 0.0); the Phase 2 report regenerates
byte-identically. One stale generated report in the archive (Phase 1 audit listed 9 warnings, current code/docs say 10) was repaired by
regeneration. **No Phase 1/2 source file was changed; nothing was retrained.**

## 2. What was built
New package `src/parkistep/twin/` (13 modules, ~3,000 lines), `configs/phase3.yaml`, four scripts (`create_twin.py`, `replay_twin.py`,
`inspect_twin.py`, `verify_twin_model.py`), seven new test files + helper (173 tests), `docs/phase3/`, README section. Components:
twin state schema and deterministic lifecycle; observation contract; bounded rolling buffer; ordering/duplicate/gap/session policies;
model gateway over the existing `ForecastPredictor` (manifest as single source of truth); cadence scheduler; immutable prediction
history; runtime warning manager; descriptive baseline; SQLite persistence with restore; replay adapter with separated ground truth;
versioned service view; CLI. Details in `PHASE3_IMPLEMENTATION.md`.

Defects found **by the new tests while building** and fixed (not left for later): (a) warning-manager boundary handling differed from
Phase 2's `raise_alerts` in 129/1500 random sequences (float-tolerance and interrupted-series semantics) - now 0/1500 and permanently tested;
(b) the baseline's cold-start variance was under-estimated, which would have frozen it via its own outlier safeguard - replaced by a
bias-corrected start; (c) clinical values were written to the database even with `store_clinical_values: false` (create path bypassed
minimisation) - single persistence path now; (d) the persisted snapshot could lag the in-memory twin - `close()` now writes a final snapshot.

## 3. Tests executed (final run, `PARKISTEP_REQUIRE_REAL=1`, `-rs`)
| Group | Tests | Result |
|---|---:|---|
| Phase 1 + Phase 2 (unchanged) | 109 | 109 passed, 0 skipped |
| Phase 3 ingestion/buffer (`_buffer`) | 27 | passed |
| Phase 3 state/lifecycle/forecast integration, test double (`_engine`) | 46 | passed |
| Phase 3 warning manager incl. Phase 2 equivalence (`_alerts`) | 23 | passed |
| Phase 3 persistence (`_persistence`) | 16 | passed |
| Phase 3 configuration + CLI + hard-coding guard (`_config`) | 31 | passed |
| Phase 3 replay, known-answer comparison (`_replay`) | 8 | passed |
| Phase 3 genuine trained artifacts on synthetic data (`_real_model`) | 11 | passed |
| Phase 3 **real FoG-STAR + real saved artifacts** (`_real_data`) | 11 | passed |
| **Total** | **282** | **282 passed, 0 failed, 0 skipped** |

Commands: `python -m pytest` (about 3 minutes; run in chunks if your shell limits call time). Without `PARKISTEP_REQUIRE_REAL=1`
the 11 real-data tests skip with a stated reason when data/artifacts are absent; here none skipped.

**Test-quality check.** Seven safeguards were deliberately broken one at a time (buffer not cleared on session change; quality gate bypassed;
threshold overridden; over-long window; patient check removed; clinical passed to a sensor-only model; stale forecast kept "current"):
each break was caught by a failing test, and the engine file was then restored byte-for-byte.

## 4. Real model integration
* Configured model: `fog_onset_forecast_6499e95d / sensor_only / random_forest`, horizon 3.0 s, SHA-256 `55aee24bcf2325b2...` verified, operating threshold 0.7032 read from the manifest (`threshold_source: model_manifest`), window 2.0 s = 121 samples at 1/60 s, cadence 0.2 s (all from the manifest).
* `verify_twin_model.py` (real recording, real trailing window): exit 0, score 0.7283, above threshold, data quality ok.
* Real fusion artifact (`fusion / random_forest / h3`): `verify-model` exit 0; a 2,500-observation real replay produced 199 forecasts, all with `clinical_used=true`, threshold 0.6212, 0 future-data violations.
* Refusals confirmed with real artifacts: horizon 4.0 s (no such artifact) -> exit 1 with the list of available artifacts, **no substitution**; invalid configuration -> exit 2; tampered artifact (copy) -> checksum failure; manifest without model file -> failure.
* Engine forecasts equal direct `ForecastPredictor.predict` calls on the same trailing window to 1e-12 (synthetic-trained and real artifacts).

## 5. Replay results (real recording, real model) - software integration only
Default configuration; selection policy `most_events` chose participant 2, recording `p2-s1-t4` (11,176 samples, 186 s).
| Item | Actual value |
|---|---|
| Observations offered / processed / rejected | 11,176 / 11,176 / 0 |
| Time per status | WARMING_UP 120 observations, READY 11,056 |
| Forecasts | 922 (= (11,176 - 121)/12 + 1, the exact cadence grid); none unavailable |
| Warnings activated | 14 (all resolved), 10 `EXTENDED` merge events, 0 suppressed |
| Future-data check | 922 forecasts, **0 violations** (every window ends at the triggering sample) |
| Inference time | mean 19 ms, max 71 ms (wall time of the whole replay 23 s on this machine) |
| Reproducibility | two independent runs: identical determinism digest `13ee0b1870bc054f...` |
| Overwrite protection | repeating the persisted replay: exit 1, `TwinExistsError` |

Retrospective comparison with the annotations (made only after the stream ended; **in-sample**, since the saved model was fitted on all participants including this one):
7 annotated events; **1 detected** (sensitivity 0.14; lead 2.4 s); 1 valid early warning; 3 false warnings; 10 of 14 warnings excluded because
their input window overlapped an annotated episode (Phase 2 scoring rule); 409 forecasts scored (0.023 h, so any per-hour rate here, e.g. the 132
false warnings/h that arithmetic gives, rests on 82 s of scored time and is not meaningful). **Even in-sample the model detected 1 of 7 onsets**,
which agrees with Phase 2's finding of low and variable sensitivity. This replay shows that the engine works; it is not evidence that the forecaster does.

## 6. Runtime-state examples from actual runs
Lifecycle audit trail of the replay: `INITIALIZING -> WAITING_FOR_DATA` (twin created) -> `SESSION_START p2-s1-t4` ->
`WARMING_UP` (first sample) -> `READY` ("the window satisfies the model's input requirements") -> `SESSION_END` (replay_finished) -> `WAITING_FOR_DATA`.

First warning record (`reports/phase3/replay_p2_p2-s1-t4_warnings.json`): `twin-2-w0001`, activated at t = 566.68 s by prediction `twin-2-p000059`,
threshold 0.7032 (`model_manifest`), horizon 3.0 s, 18 qualifying forecasts, max score 0.793, resolved at t = 571.28 s ("no qualifying forecast within
the merge gap; score below threshold"); lifecycle `ACTIVE -> RESOLVED`. The first annotated onset (569.08 s) lies 2.4 s after the activation.

After `inspect_twin.py` (state restored from SQLite): twin `twin-2`, status `WAITING_FOR_DATA`, `forecast_status: historical`, `is_current: false`,
`current_forecast: null`, 922 historical forecasts, last computed score 0.5243 (below threshold), 14 warnings in history, warning state `none`,
baseline `reporting` with 226 updates, clinical record `valid`, `influences_forecast: false` (sensor-only model). `create_twin.py --patient 3`:
status `WAITING_FOR_DATA`, clinical record read from the real Phase 1 table (`valid`, no missing fields).

## 7. Persistence
SQLite store 1.4 MB for this replay (922 predictions, 14 warnings, audit events; raw observations not stored by default). Restore verified by tests and the
real CLI round-trip: history identical, nothing presented as current, active warning expired with reason `restored_without_live_data`, warm-up restarts,
numbering continues, corrupt/foreign/incompatible state refused, clinical values minimised by default and hash-checked on restore.

## 8. Outstanding issues and known limitations
1. **Predictive performance is weak** (Phase 2): sensitivity 0.21 [0.04-0.35] and ~74 false alerts/h at 3 s for the default model; no demonstrated benefit of fusion. Nothing in Phase 3 improves this and it must not be presented as clinical.
2. Replays of dataset recordings are in-sample (models fitted on all participants). Honest out-of-sample behaviour is only available from the Phase 2 participant-grouped evaluation.
3. Scores are uncalibrated; warnings are policy outputs. The warning policy used by default is the one Phase 2 evaluated (persistence 1, merge 1 s, cooldown 0) - it does not suppress false alerts.
4. Not run: a multi-recording `scope: participant` replay through the CLI (a real two-recording boundary replay is covered by a test using 400 + 500 real samples, and the synthetic boundary tests); a replay with `speed > 0` against the real data (timing is covered with an injected clock); no live-wearable adapter (the `SensorStream` interface is ready for one).
5. Single-process engine (lock-protected, no distributed state); persisted twins restart warm-up by design; the baseline is descriptive and unvalidated.
6. Zenodo version tag still unverifiable offline (carried from Phase 2).
7. Inference is equal-or-stricter than training at window edges (harmless).

## 9. Acceptance gates
| Gate | Status | Evidence |
|---|---|---|
| 1 Previous phases healthy | **Met** | 109 tests, audit reproduced, 24/24 artifacts verified, real inference works |
| 2 Twin can be initialised | **Met** | validated clinical profile; schema includes identity/lifecycle/quality/predictions; isolation and honest missing-data tests |
| 3 New observations update the twin | **Met** | incremental ingestion, bounded time-aware buffer, boundaries respected (synthetic + real), invalid windows never reach the model, cadence grid verified |
| 4 Phase 2 inference integrated correctly | **Met** | real artifacts, exact horizon match, manifest schema, clinical only for fusion, complete metadata, 0 future-data violations |
| 5 Warning management works | **Met** | manifest threshold (not overridable), persistence/cooldown/merge/expiry, triggering prediction recorded, equals Phase 2 `raise_alerts` on 1,500 sequences |
| 6 Replay end to end | **Met** | real recording replayed sequentially; state/forecasts/warnings recorded; labels separate and used only afterwards; reproducible digest |
| 7 Reliability and reproducibility | **Met** | persistence + restore, explicit errors, externalised configuration (static guard), coherent audit log, 282 tests, docs match code |

All gates are met as *engineering* gates. None of them asserts clinical validity.
