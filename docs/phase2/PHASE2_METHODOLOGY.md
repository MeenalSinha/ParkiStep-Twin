# Phase 2 - forecasting target and baseline models

Research proof of concept. Model outputs are scores, not diagnoses.

## Problem and target
At prediction anchor `t` (a sample of one recording) the model sees only the trailing window `[t-L, t]` and estimates the probability that a **new** annotated freezing episode **starts** in `(t, t+H]`. `L`, `H`, the anchor stride and every threshold come from `configs/phase2.yaml`.

* **Event** = contiguous run of `fog == 1` inside one recording (`events.py`); onset = first positive sample time. Events are never merged unless `events.merge_gap_s > 0`.
* **Positive**: an onset falls in `(t, t+H]`. **Negative**: no onset there *and* the whole interval is observed (`require_full_future`); otherwise the anchor is *censored* and dropped.
* **Excluded anchors** (counted per reason, `window_summary.json`): `insufficient_history`, `timestamp_irregular_window` (window span deviates from the span expected from the recording's own median step), `ongoing_event_at_anchor`, `input_window_overlaps_event`, optional `too_soon_after_previous_event`, `task/activity_not_eligible`, `censored_future`. An ongoing or just-finished freeze therefore can never produce a prediction, so detection cannot masquerade as forecasting.
* Windows are cut per `recording_id`. The dataset timestamp is one running clock across recordings, so timestamp continuity is never used to define a boundary. Sample counts derive from each recording's measured step, not an assumed rate.

## Features (`features.py`)
Causal, deterministic, computed from the trailing window only (annotations are not an argument of the extractor): per channel (24 raw + 8 magnitude channels) mean, std, min, max, rms, q25, median, q75, range, mean absolute difference; spectral features (dominant frequency, energy in configurable bands, freeze-index style log band ratio, spectral entropy, total power); ankle left/right cross-sensor correlation and log std ratio. Isolated missing samples are filled by bounded forward-fill only (past values); a sensor position is usable only if each channel has `>= min_valid_fraction` raw valid samples and no NaN remains. Sensor sets (ablations) are config-defined position groups; features needing a missing position are dropped.
Clinical predictors (config list): age, gender, disease_duration, hoehn_yahr, updrs_iii, fog_q, moca, pdq_8. `fes_i` is excluded because its values are inconsistent with the documented range.

## Models
`dummy_prior` (prevalence), `dummy_random` (uniform random scores, chance-level alerting reference), regularised `logistic_regression`, `random_forest` (shallow, leaf-size regularised). Imputation, scaling and one-hot encoding live inside the sklearn pipeline and are fitted on training rows only. Optional subject-balanced sample weights give every training participant equal total weight; class weights are configurable. Every second anchor is used for fitting (`train_stride_multiplier`) because neighbouring anchors are near-duplicates; evaluation uses all eligible anchors.
Experiments: A `sensor_only`, B `fusion` (sensor features + clinical features, feature-level fusion); same target, folds, seeds and evaluation.

## Evaluation (`models.py`, `alerts.py`, `evaluate.py`)
* **Participant-grouped folds**: greedy event-balanced assignment (deterministic), same folds for all models/horizons; each participant is tested exactly once by a model that never saw them (`split_manifest.csv`, OOF predictions retain window/recording/participant/event ids).
* **Operating threshold**: chosen inside each training partition from inner participant-grouped out-of-fold predictions (policy `fa_budget`: highest event sensitivity with false alerts/h <= budget; alternatives `max_fbeta`, `fixed`). Test labels are never used.
* **Alert processing**: consecutive above-threshold anchors form a run; `persistence_anchors`, `merge_gap_s`, `cooldown_s` configurable. The alert time is the time of the anchor that completes persistence.
* **Matching**: an alert at `a` is a valid warning for onset `E` iff `min_lead <= E - a <= H`; the earliest valid alert detects the event, lead = `E - a`. Alerts valid for no onset (any annotated event, evaluable or not) are false; extra alerts for a detected event are ignored or counted per config.
* **Metrics**: AP, ROC-AUC, Brier (with prevalence reference), calibration bins, precision/recall/F1 at the selected thresholds; event sensitivity over *evaluable* events (events with at least one eligible positive anchor); false alerts/h = unmatched alerts / (eligible anchors x stride) in hours; lead-time quantiles; fold and participant tables; participant-level bootstrap (percentile CIs, paired deltas fusion - sensor-only using identical resamples).

## Artifacts and inference
`artifacts/phase2/<experiment_id>/`: `config_snapshot.json`, `software_versions.json`, `dataset_provenance.json`, `split_manifest.csv`, `oof/*.parquet` (+ thresholds), `models/*.joblib` + `*.manifest.json` (feature manifest, feature definitions, window settings, threshold, SHA-256), `failures.json`, `train.log`, `phase2_metrics.json`. Final models are fitted on all participants for the inference interface only; they are never used for reported metrics. `ForecastPredictor.load()` verifies the checksum before unpickling - load only artifacts you produced.
`ForecastPredictor.predict(window_df, timestamp_col, clinical=None, metadata=None)` validates columns, timestamp order/gaps and required duration, returns a `ForecastResult` (probability, horizon, prediction time, model id, data-quality status, warnings, feature availability, threshold flag, metadata). Sensor-only models ignore clinical input (with a warning); fusion models apply the trained imputation policy to missing clinical fields (with a warning) and never invent values.

## Commands
```bash
python scripts/prepare_data.py                      # Phase 1 (audit + processed tables)
python scripts/prepare_phase2_windows.py            # events, anchors/labels, features  (--force to rebuild)
python scripts/train_phase2.py                      # grouped CV + final models (resumable; --force retrains)
python scripts/evaluate_phase2.py                   # metrics + reports/phase2/
python -m pytest
# any setting can be overridden:  --set windowing.horizons_s=[2.0,4.0] --set windowing.input_window_s=3
```

## Configuration reference (`configs/phase2.yaml`)
`data` (clinical predictors, task/activity restriction, sensor sets) - `events` (merge/min duration) - `windowing` (L, horizons, stride, tolerance, coverage, fill limit, exclusion rules) - `features` (families, bands, derived/cross channels, exclusions) - `models` - `experiments` - `preprocessing` - `training` (seed, weighting, subsampling) - `split` - `threshold` - `alerts` - `evaluation` (bootstrap, calibration, selection) - `outputs` - `logging`. Validation (`forecast/settings.py`) rejects unknown models/features/sensors, identifier or annotation fields as predictors, and inconsistent experiment definitions. Dataset column names live only in `configs/datasets.yaml`.

## Known limitations
22 participants (16 with events) and ~100 events: independent information is small, window-level counts are strongly correlated, CIs are wide. Laboratory tasks, 10 fps annotations, per-session sensor dropouts, a total monitored time of only a few hours (false-alert rates per hour are extrapolations). Not validated for clinical use. FoG-STAR citation: Borzì, L. et al. (2026) Sci Data 13, 305, https://doi.org/10.1038/s41597-026-06645-1; Zenodo https://zenodo.org/records/17838806 (CC BY 4.0). Daphnet was not used.
