# Phase 3 - Digital-twin engine: implementation guide

> Outputs of this system are **research model scores, not diagnoses**. The Phase 2 model is trained on 22 participants,
> is not clinically validated, and its scores are uncalibrated. A "warning" means a score met a configured policy; it does not
> establish that a freezing episode is occurring or will occur. Phase 3 is the engine; the clinician dashboard is a later phase.

## 1. What it does
For each patient the engine keeps an isolated **twin**: identity, clinical context, stream state, forecast state, warning
state and a descriptive movement baseline. Sensor observations are ingested one at a time; each accepted observation updates
the twin. When the rolling window satisfies the Phase 2 model's documented requirements and a forecast is due, the engine calls
the existing `ForecastPredictor` on the trailing window (and nothing else), records an immutable `PredictionRecord`, applies the
warning policy and persists the history. Feature extraction, preprocessing, model inference and the operating threshold all stay
in Phase 2; Phase 3 never re-implements them.

## 2. Package layout (`src/parkistep/twin/`)
| Module | Responsibility |
|---|---|
| `config.py` | Load/validate `configs/phase3.yaml` (+ `--set` overrides); config and state-compatibility hashes |
| `model.py` | Load the configured artifact through `ForecastPredictor` (checksum), compatibility checks, window requirements and threshold provenance from the manifest |
| `state.py` | `TwinState` and sub-states, immutable `PredictionRecord`, `TwinStatus`, `derive_status` (pure lifecycle rule) |
| `buffer.py` | Observation contract/mapper, optional `ReorderStage`, bounded `RollingBuffer`, `assess_window` (reuses Phase 2 `causal_fill`/`position_quality`) |
| `alerts.py` | `WarningManager` (pending/active/resolved/expired), `WarningPolicy`, `WarningRecord` |
| `baseline.py` | Optional descriptive rolling movement baseline |
| `engine.py` | `TwinEngine`: orchestration, lifecycle, scheduling, persistence hooks, restore |
| `storage.py` | `StateStore` (SQLite, or in-memory with identical code) |
| `streams.py` | `SensorStream` interface, `DataFrameStream`, replay selection/stream builder, separate `GroundTruth`, `Phase1ClinicalSource` |
| `replay.py` | `ReplayRunner` (sequential replay, retrospective comparison, output files) |
| `service.py` | `build_engine`, `TwinService.view()` - the versioned application-facing view |
| `cli.py` | `create`, `replay`, `inspect`, `verify-model` |

## 3. State schema (`parkistep.twin.state/1`)
`TwinState` sections (JSON via `to_dict()`):

* **identity/lifecycle** - `twin_id` (template `patient.twin_id_template`), `patient_id`, `created_at`, `updated_at`, `update_seq`, `status`, `status_reason`, `status_since_ts`, `restored`.
* **clinical** - `status` (valid / partial / absent), `values`, `missing_fields`, per-field `validation` (ok / missing / out_of_range, from the dataset profile's ranges), `provenance` (source, key, row hash, dataset), `influences_forecast` (true only when the selected model takes clinical input). Missing fields are never filled in.
* **stream** - `session_id`, `latest_observation_ts`, `latest_values`, buffer coverage, `quality_status` and per-position valid fractions, `missing_channels`, gap indicators, accepted/rejected counters (by reason).
* **forecast** - `status` (`none` / `current` / `stale` / `unavailable` / `historical`) with reason, latest prediction id/time, next due time, counters.
* **warning** - `state` (`none` / `pending` / `active`), active warning id, pending count, counters (including suppressed warnings).
* **baseline** - descriptive statistics (section 8).
* **model**, **config** - model identity/threshold/window, config hashes, active warning policy.

Four things are kept apart: recorded clinical history (`clinical`), statistics derived from observed movement (`baseline`),
learned Phase 2 parameters (never stored in the twin, only the model identity), and Phase 2 predictions (`PredictionRecord`).
No per-patient model is trained: all twins share the one Phase 2 artifact.

`PredictionRecord` (frozen dataclass): `prediction_id` (`<twin_id>-p000123`), twin/patient/session ids, `prediction_time` and
window start/end (observation clock), `probability` (a score), `horizon_s`, model id + artifact SHA-256, `uses_clinical` /
`clinical_used`, `threshold` + `threshold_source`, `above_threshold`, `data_quality`, quality detail, predictor warnings,
`inference_ms`, `processing_time_utc`, `state_version`, `score_semantics`, `disclaimer`. Records are never modified; a new
forecast adds a new record.

## 4. Lifecycle
`derive_status(facts)` is a pure function; first match wins:

| Status | Meaning (software/data condition, **not** medical) |
|---|---|
| `ERROR` | an unexpected exception occurred in the last update (cleared by the next successful update when `lifecycle.error_recovery=auto`) |
| `MODEL_UNAVAILABLE` | the selected model could not be loaded; data are tracked, no forecasts |
| `STALE` | no accepted observation within `lifecycle.stale_after_s` (observed via `tick()`) |
| `WAITING_FOR_DATA` | no observation in the current session |
| `WARMING_UP` | the window does not yet hold enough contiguous samples (or warm-up time) |
| `DEGRADED_DATA_QUALITY` | window complete but sensor quality (or required clinical fields) below requirements |
| `READY` | the window satisfies the model's requirements |
| `INITIALIZING` | only during creation; every transition is written to the audit log with its reason |

## 5. Observation contract and update step
Record fields are configured (`observation.*`): patient field, timestamp (numeric with unit, or ISO datetime with a configured
timezone for naive values), session field(s), and sensor channels (identity or an explicit source->canonical mapping). Values
must convert to float; `NaN`/`None` mean "missing". Every incoming record yields an `IngestResult` (`accepted` / `held` /
`rejected` / `ignored`, with a reason code from: `PATIENT_MISMATCH`, `MISSING_FIELD`, `INVALID_TIMESTAMP`, `INVALID_VALUE`,
`MISSING_CHANNEL`, `DUPLICATE_TIMESTAMP`, `CONFLICTING_DUPLICATE`, `OUT_OF_ORDER`, `TOO_LATE_TO_REORDER`,
`SESSION_CHANGE_REQUIRES_END`). Rejections change no twin data beyond counters/audit events.

Per accepted observation: patient check -> session check -> order/duplicate/gap checks -> append to the buffer -> assess the
window -> if a forecast is due: eligible? -> `ForecastPredictor.predict(trailing window)` -> prediction history -> warning
policy -> baseline -> lifecycle status -> persistence -> result. `ingest_many` is literally sequential `ingest` (tested).

* **Ordering.** `reject` (default; arrival order is never hidden) or `reorder_within_tolerance` (bounded hold-back; the
  arrival sequence is kept and `OBSERVATION_REORDERED` is audited; adds `reorder_tolerance_s` of latency; a sample older than
  what was already released is `TOO_LATE_TO_REORDER`).
* **Duplicates.** `reject`, or `ignore_identical` (identical payload is ignored; a different payload at the same timestamp is
  `CONFLICTING_DUPLICATE`).
* **Gaps.** A step larger than `max_gap_s` (default `gap_threshold_factor x dt` from the dataset profile) is audited. Policy
  `reset_buffer` clears the window (warm-up restarts; the forecast series is marked interrupted, so any pending run is cleared and an
  active warning expires once `warning.expire_after_invalid_s` passes without a valid forecast); `flag_only` keeps samples but the
  window is ineligible until the gap leaves it.
* **Sessions.** Recording identity comes from the session fields, never from timestamps (FoG-STAR timestamps are continuous
  across recordings). A change closes the old session (audited, warning expired, cooldown reset), clears the buffer and opens a
  new one; `require_explicit_end` instead rejects until `end_session()`.
* **Window eligibility** (`assess_window`, from the manifest): enough samples, no gap, span consistent with the model's sampling
  interval, warm-up satisfied, per-position valid fraction and forward-fill rule via Phase 2 `position_quality`. Failure =>
  the model is **not called**, `forecast.status = unavailable` with the reason, the previous record stays history.
* **Cadence.** Due times lie on a grid anchored at the first eligible window with spacing `cadence.every_s` (default: the
  manifest's stride). Inference is synchronous and single-slot: no queue of pending requests, no window scored twice.
* **Staleness.** A forecast older than `forecast.stale_after_cadences x cadence` becomes `stale`; `tick(now)` also applies the stream-level `STALE` rule and warning expiry.

## 6. Model loading and compatibility (`model.py`)
`load_model` locates `<experiment>__<model>__<horizon key>.manifest.json` in `model.artifact_dir` (default: the Phase 2 artifact
directory), requires its `.joblib`, verifies the SHA-256 (via `ForecastPredictor.load`) and then checks: experiment/model/horizon
match the configuration **exactly** (otherwise `ModelUnavailableError`; no other horizon or model is substituted); manifest keys
present; sensor positions exist in the dataset profile; the observation channel configuration covers the model's channels; the
buffer is large and long enough; `buffer.min_valid_fraction` is not weaker than the model's; clinical fields map to the profile;
the threshold is finite. Window length, sampling interval, feature schema, horizon, threshold and alert policy are read from the
manifest and are not duplicated in `phase3.yaml`. `model.on_unavailable`: `fail` raises; `degrade` runs the twin in
`MODEL_UNAVAILABLE`.

Sensor-only models receive no clinical input and are never credited with it (`clinical_used=false`, `influences_forecast=false`).
Fusion models receive the model's clinical fields from the patient's profile (mapping configurable); missing values pass through
as missing so the **saved** imputation policy applies and the predictor's warnings are stored on the record, unless
`patient.clinical_missing_policy=require_all`, which withholds the forecast instead.

## 7. Warning lifecycle (`alerts.py`)
Threshold: always the one stored with the artifact (`threshold_source=model_manifest`). If it is missing/invalid, warnings are
unavailable (`WARNING_THRESHOLD_UNAVAILABLE`) unless `warning.threshold.fallback_value` is configured, in which case records say
`configured_fallback`. A configured value never overrides a valid manifest threshold.

Policy (`warning.policy_source`): `model_manifest` uses the persistence / merge gap / cooldown saved with the model - the same
values Phase 2 evaluated - or `config` uses the values in the file (then Phase 2's measured sensitivity does not describe it).
Semantics: consecutive above-threshold forecasts (forecasts more than 1.5 cadences apart are not consecutive) form a *run*; a run
of `persistence_anchors` activates a warning at the completing forecast (`pending` before that); qualifying runs starting within
`merge_gap_s` of the previous one extend the same warning; an activation within `cooldown_s` of the previous one is suppressed
(and audited); a warning *resolves* at the first forecast evaluated after `merge_gap_s` without qualifying forecasts and after
`clear.consecutive_below` below-threshold forecasts (an interrupted series also satisfies this). Invalid/missing forecasts never
create warnings, clear any pending run and, after `expire_after_invalid_s`, expire an active warning; `max_active_s` bounds a
warning's age; a session end expires it; a restore expires it (`restored_without_live_data`). Warnings keep an append-only
lifecycle list and can be acknowledged. **This runtime logic is tested to reproduce the alert times of Phase 2's retrospective
`raise_alerts` on 1,500 random score sequences** (persistence 1-3, merging, cooldown, interrupted series), so the two cannot drift.

## 8. Movement baseline (`baseline.py`) - descriptive only
Exponentially weighted mean/variance (bias-corrected start) of the std (or RMS) of acceleration/gyro **magnitude** per sensor
position over each forecast window that already passed the quality gate. Updates only in `update_only_when_status`; frozen
while a warning is pending/active; windows with |z| above `skip_if_abs_z_above` (once `min_updates` is reached) are not absorbed;
incomplete windows are skipped, never imputed; no annotations or future data are involved; reset on session change is optional.
It is not a model input, not a personalised disease model, and not clinically validated.

## 9. Persistence (`storage.py`)
SQLite (`persistence.backend=sqlite`) or in-memory with the same code. Tables: `twins` (snapshot + model identity + compat hash),
`predictions`, `warnings`, `events` (status changes, sessions, quality, warning events, errors - the audit trail),
`observations` (only if `store_observations`, bounded), `meta` (config snapshot, model identity). Every row is keyed by twin id.
`snapshot_policy`: `on_event` (default; `close()` writes a final snapshot), `every_update`, `manual`. Clinical values are **not**
stored by default (reference + row hash only; values are re-read from the clinical source on restore and the hash verified).
Retention: `retention.*` for in-memory histories and stored rows.

**Restore** (`restore_twin`) checks schema version, model id + artifact checksum, and the state-affecting configuration hash
(`persistence.on_incompatible=allow_config_change` to override). The rolling buffer is **not** restored: the twin re-enters
warm-up, old forecasts become `historical` (never `current`), an active warning is expired, numbering continues without collisions.
Corrupt or foreign snapshots raise `StateStoreError`.

## 10. Replay (`streams.py`, `replay.py`)
`RecordingReplayStream` emits only the identifier, timestamp, session and sensor columns of the selected recording(s); `fog`,
`fog_severity`, `activity` and task are never in a record. `GroundTruth` is built separately and consulted only after the stream
ends. Selection comes from config (`replay.participant_id`, `recording_id`, or `selection_policy` `most_events|longest|first`;
`scope` `recording|participant`). `speed=0` replays as fast as possible; `speed>0` sleeps in proportion to timestamp differences.
The summary reports actual counts, status time shares, a future-data check (every window must end at the triggering sample),
timing, a determinism digest (hash of the non-wall-clock content), and - after the replay - how warnings related to annotated
onsets, using the Phase 2 matching rules: warnings whose input window overlaps an annotated episode, or whose horizon runs past
the recording end, are excluded, as in Phase 2. **Saved models are fitted on all participants, so this comparison is in-sample.**

## 11. Commands
```bash
python scripts/verify_twin_model.py   # artifact gate: file, checksum, schema, real prediction on a real window
python scripts/create_twin.py  --patient 3                      # new twin (clinical record from the Phase 1 table)
python scripts/replay_twin.py                                    # replay the selected real recording; writes reports/phase3/
python scripts/inspect_twin.py --list | --patient 2 [--predictions 10] [--events STATUS_CHANGE]
# any setting: --set section.key=value, e.g.  --set model.experiment=fusion --set model.model=logistic --set model.horizon_s=5.0
```
Exit codes: 0 success, 1 runtime/integrity failure (missing dataset or model, persisted twin already exists, incompatible state,
future-data violation), 2 configuration error.

## 12. Configuration reference
All keys are in `configs/phase3.yaml` with inline comments. Groups: `patient` (id field/pattern, twin-id template, exists policy,
clinical source/mapping/missing policy), `observation` (field names, timestamp kind/unit/timezone, channel mapping), `ingestion`
(duplicate / out-of-order / gap / missing-channel / non-finite / session policies, `max_gap_s`), `buffer` (retention duration
and count, warm-up extra, stricter valid fraction), `model` (artifact dir, experiment, model, horizon, `on_unavailable`, dt
source), `cadence`, `forecast` (staleness), `warning` (policy source, persistence, merge gap, cooldown, clear, repeat, expiry,
threshold fallback, suppressed statuses), `baseline`, `lifecycle`, `persistence`, `retention`, `logging`, `replay`, `outputs`.
`null` means "derive from the model manifest / dataset profile". Validation rejects unknown choices, non-positive numbers,
unknown statuses, incompatible horizons and buffers too small for the model, with a message naming the setting. A static test
scans the engine source for hard-coded sensor/column names, paths and model names.

## 13. Error conditions
`ConfigError` (exit 2); `ModelUnavailableError` (missing/corrupt/incompatible artifact; manifest without model file; horizon
mismatch); `PatientMismatchError` (clinical record of another patient or without a verifiable key); `UnknownTwinError`;
`TwinExistsError`; `StateStoreError` / `StateIncompatibleError` (corrupt or incompatible persisted state); unexpected exceptions
during an update set `ERROR`, are audited (traceback optional) and do not stop the engine.

## 14. Testing
```bash
python -m pytest                                  # everything (about 3 minutes)
PARKISTEP_REQUIRE_REAL=1 python -m pytest         # real-data/artifact tests FAIL instead of skipping when prerequisites are missing
```
Phase 3 files: `test_phase3_buffer.py` (ingestion/buffer), `_engine.py` (state, lifecycle, forecast integration with a scripted
**test double**), `_alerts.py` (incl. Phase 2 equivalence), `_persistence.py`, `_replay.py` (replay timing, leak guard, known-answer retrospective classification), `_config.py`, `_real_model.py` (genuine trained
sklearn artifacts on synthetic data), `_real_data.py` (real FoG-STAR + real artifacts). Synthetic fixtures are never reported as
real data; the test double is never reported as a trained model.

## 15. Known limitations
Research prototype; one shared model for all patients (no personalisation); small cohort and laboratory tasks; low/variable
Phase 2 sensitivity and high false-alert rates; uncalibrated scores; replays of dataset recordings are in-sample; single-process
engine (an `RLock`, no distributed state); persisted twins restart warm-up; no live-device adapter (the `SensorStream` interface
is ready for one); processing-time stamps are wall-clock in live mode and `obs-clock:<t>` in deterministic replay mode; the
baseline is descriptive; not a medical device and no privacy/regulatory compliance is claimed.
