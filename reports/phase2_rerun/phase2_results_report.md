# ParkiStep Twin - Phase 2 results (experiment `fog_onset_forecast_90ecd04f`)

All numbers below are computed from the saved out-of-fold predictions of this run; nothing is hand-entered. Retrospective results on 22 participants are **not** evidence of clinical effectiveness.

## 1. Data and design

- Dataset: FoG-STAR: Freezing of Gait Severity, Tasks, Activities, and Ratings, version 3.0 (as stated in the bundled README; the Zenodo record's own version tag could not be verified offline); https://zenodo.org/records/17838806; license CC BY 4.0.
- Participants contributing eligible windows: 22; annotated events: 101 (9 run to a recording end); events with at least one eligible positive window before the sensor-quality filter: 97; evaluable events after the filter (all four sensors valid): 75.
- Input window 2.0 s, anchor stride 0.2 s, horizons [1.0, 3.0, 5.0] s; windows containing or following an ongoing FoG sample are excluded; futures that are not fully observed are censored.
- Split: `balanced_group_kfold`, 5 participant-grouped folds (seed 20260101). Operating threshold policy `fa_budget` (budget 30.0 false alerts/h) chosen from inner grouped out-of-fold predictions of the training participants only.
- Alert policy: persistence 1 anchor(s), merge gap 1.0 s, cooldown 0.0 s, minimum lead 0.0 s. Event sensitivity = detected evaluable events / evaluable events; false alerts/h = alerts not valid for any annotated onset / (eligible anchors x stride) in hours.
- Bootstrap: 300 participant resamples, 95% percentile intervals (resampling unit = participant).

## 2. Target construction per horizon

| Horizon (s) | eligible windows | positive | negative | positive % | events with a positive window | events without | participants with positives | monitored h |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1.0 | 19716 | 474 | 19242 | 2.40 | 97 | 4 | 15 | 1.10 |
| 3.0 | 18877 | 1361 | 17516 | 7.21 | 97 | 4 | 15 | 1.05 |
| 5.0 | 18051 | 2135 | 15916 | 11.83 | 97 | 4 | 15 | 1.00 |

Base exclusions (all anchors): eligible_base: 20154, ongoing_event_at_anchor: 5368, insufficient_history: 1060, input_window_overlaps_event: 892.

- 1.0 s: status counts {'eligible': 19716, 'excluded_base': 7320, 'censored_future': 438}; events without a positive window: {'insufficient_history_before_onset': 2, 'preceded_by_another_event': 2}.

- 3.0 s: status counts {'eligible': 18877, 'excluded_base': 7320, 'censored_future': 1277}; events without a positive window: {'insufficient_history_before_onset': 2, 'preceded_by_another_event': 2}.

- 5.0 s: status counts {'eligible': 18051, 'excluded_base': 7320, 'censored_future': 2103}; events without a positive window: {'insufficient_history_before_onset': 2, 'mixed_exclusions': 1, 'preceded_by_another_event': 1}.

These counts are before the per-experiment sensor-quality filter; the number of windows actually used is shown in section 3.

## 3. Results by horizon

### Horizon 1.0 s

| Experiment | Model | windows (pos) | evaluable events | AP | ROC-AUC | Brier (prev. ref.) | event sensitivity [CI] | false alerts/h [CI] | median lead s [CI] | P/R/F1 at selected thr. |
|---|---|---:|---:|---|---:|---|---|---|---|---|
| sensor_only | dummy_prior | 16919 (372) | 75 | 0.021 [0.012, 0.037] | 0.466 | 0.0216 (0.0215) | 0.00 [0.00, 0.00] (0/75) | 0.0 [0.0, 0.0] | n/a | n/a/0.00/n/a |
| sensor_only | dummy_random | 16919 (372) | 75 | 0.024 [0.014, 0.037] | 0.517 | 0.3328 (0.0215) | 0.00 [0.00, 0.00] (0/75) | 14.9 [8.2, 22.0] | n/a | 0.00/0.00/0.00 |
| sensor_only | logistic | 16919 (372) | 75 | 0.054 [0.022, 0.089] | 0.696 | 0.1172 (0.0215) | 0.01 [0.00, 0.04] (1/75) | 5.3 [0.0, 14.9] | 1.00 [1.00, 1.00] | 0.20/0.01/0.01 |
| sensor_only | random_forest | 16919 (372) | 75 | 0.092 [0.037, 0.151] | 0.730 | 0.0480 (0.0215) | 0.11 [0.00, 0.19] (8/75) | 29.8 [7.4, 55.6] | 0.60 [0.40, 0.90] | 0.21/0.08/0.12 |
| fusion | dummy_prior | 16919 (372) | 75 | 0.021 [0.012, 0.037] | 0.466 | 0.0216 (0.0215) | 0.00 [0.00, 0.00] (0/75) | 0.0 [0.0, 0.0] | n/a | n/a/0.00/n/a |
| fusion | dummy_random | 16919 (372) | 75 | 0.024 [0.014, 0.037] | 0.517 | 0.3328 (0.0215) | 0.00 [0.00, 0.00] (0/75) | 14.9 [8.2, 22.0] | n/a | 0.00/0.00/0.00 |
| fusion | logistic | 16919 (372) | 75 | 0.060 [0.023, 0.099] | 0.693 | 0.1226 (0.0215) | 0.00 [0.00, 0.00] (0/75) | 4.3 [0.0, 11.6] | n/a | 0.00/0.00/0.00 |
| fusion | random_forest | 16919 (372) | 75 | 0.084 [0.032, 0.134] | 0.728 | 0.0505 (0.0215) | 0.07 [0.00, 0.16] (5/75) | 44.7 [8.5, 85.0] | 0.60 [0.40, 1.00] | 0.13/0.11/0.12 |

### Horizon 3.0 s

| Experiment | Model | windows (pos) | evaluable events | AP | ROC-AUC | Brier (prev. ref.) | event sensitivity [CI] | false alerts/h [CI] | median lead s [CI] | P/R/F1 at selected thr. |
|---|---|---:|---:|---|---:|---|---|---|---|---|
| sensor_only | dummy_prior | 16229 (1069) | 75 | 0.062 [0.035, 0.112] | 0.471 | 0.0623 (0.0615) | 0.00 [0.00, 0.00] (0/75) | 0.0 [0.0, 0.0] | n/a | n/a/0.00/n/a |
| sensor_only | dummy_random | 16229 (1069) | 75 | 0.063 [0.037, 0.097] | 0.484 | 0.3356 (0.0615) | 0.00 [0.00, 0.00] (0/75) | 18.9 [11.3, 27.5] | n/a | 0.00/0.00/0.00 |
| sensor_only | logistic | 16229 (1069) | 75 | 0.157 [0.065, 0.226] | 0.700 | 0.1433 (0.0615) | 0.09 [0.00, 0.23] (7/75) | 31.1 [5.1, 73.8] | 1.68 [0.52, 1.69] | 0.25/0.04/0.07 |
| sensor_only | random_forest | 16229 (1069) | 75 | 0.134 [0.072, 0.189] | 0.704 | 0.1088 (0.0615) | 0.21 [0.04, 0.35] (16/75) | 74.3 [24.2, 123.9] | 1.65 [1.00, 2.80] | 0.16/0.08/0.11 |
| fusion | dummy_prior | 16229 (1069) | 75 | 0.062 [0.035, 0.112] | 0.471 | 0.0623 (0.0615) | 0.00 [0.00, 0.00] (0/75) | 0.0 [0.0, 0.0] | n/a | n/a/0.00/n/a |
| fusion | dummy_random | 16229 (1069) | 75 | 0.063 [0.037, 0.097] | 0.484 | 0.3356 (0.0615) | 0.00 [0.00, 0.00] (0/75) | 18.9 [11.3, 27.5] | n/a | 0.00/0.00/0.00 |
| fusion | logistic | 16229 (1069) | 75 | 0.161 [0.078, 0.226] | 0.699 | 0.1640 (0.0615) | 0.03 [0.00, 0.07] (2/75) | 10.0 [1.8, 24.0] | 0.91 [0.52, 1.30] | 0.11/0.00/0.01 |
| fusion | random_forest | 16229 (1069) | 75 | 0.147 [0.075, 0.197] | 0.707 | 0.1065 (0.0615) | 0.11 [0.01, 0.24] (8/75) | 58.8 [8.4, 123.0] | 1.76 [0.18, 2.40] | 0.20/0.11/0.14 |

### Horizon 5.0 s

| Experiment | Model | windows (pos) | evaluable events | AP | ROC-AUC | Brier (prev. ref.) | event sensitivity [CI] | false alerts/h [CI] | median lead s [CI] | P/R/F1 at selected thr. |
|---|---|---:|---:|---|---:|---|---|---|---|---|
| sensor_only | dummy_prior | 15544 (1681) | 75 | 0.098 [0.057, 0.174] | 0.448 | 0.0993 (0.0964) | 0.00 [0.00, 0.00] (0/75) | 0.0 [0.0, 0.0] | n/a | n/a/0.00/n/a |
| sensor_only | dummy_random | 15544 (1681) | 75 | 0.112 [0.065, 0.171] | 0.506 | 0.3324 (0.0964) | 0.04 [0.00, 0.10] (3/75) | 11.6 [6.0, 17.5] | 3.40 [0.10, 4.20] | 0.23/0.00/0.00 |
| sensor_only | logistic | 15544 (1681) | 75 | 0.237 [0.103, 0.321] | 0.699 | 0.1698 (0.0964) | 0.08 [0.00, 0.22] (6/75) | 16.2 [0.0, 47.7] | 2.80 [2.80, 2.80] | 0.35/0.02/0.04 |
| sensor_only | random_forest | 15544 (1681) | 75 | 0.198 [0.106, 0.294] | 0.715 | 0.1277 (0.0964) | 0.07 [0.00, 0.22] (5/75) | 16.2 [0.0, 54.6] | 2.30 [2.30, 2.30] | 0.10/0.01/0.02 |
| fusion | dummy_prior | 15544 (1681) | 75 | 0.098 [0.057, 0.174] | 0.448 | 0.0993 (0.0964) | 0.00 [0.00, 0.00] (0/75) | 0.0 [0.0, 0.0] | n/a | n/a/0.00/n/a |
| fusion | dummy_random | 15544 (1681) | 75 | 0.112 [0.065, 0.171] | 0.506 | 0.3324 (0.0964) | 0.04 [0.00, 0.10] (3/75) | 11.6 [6.0, 17.5] | 3.40 [0.10, 4.20] | 0.23/0.00/0.00 |
| fusion | logistic | 15544 (1681) | 75 | 0.236 [0.107, 0.325] | 0.705 | 0.1789 (0.0964) | 0.05 [0.00, 0.15] (4/75) | 8.1 [0.0, 25.0] | 3.25 [3.25, 3.25] | 0.45/0.01/0.03 |
| fusion | random_forest | 15544 (1681) | 75 | 0.219 [0.108, 0.294] | 0.722 | 0.1287 (0.0964) | 0.05 [0.00, 0.15] (4/75) | 24.3 [0.0, 63.4] | 2.61 [2.61, 2.61] | 0.42/0.06/0.10 |

### Warning lead-time distributions (detected events, seconds before annotated onset)

| Experiment | Model | Horizon | n | min | p10 | p25 | median | p75 | p90 | max |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| fusion | dummy_random | 5.0 | 3 | 0.10 | 0.76 | 1.75 | 3.40 | 4.20 | 4.68 | 5.00 |
| fusion | logistic | 3.0 | 2 | 0.52 | 0.60 | 0.71 | 0.91 | 1.10 | 1.22 | 1.30 |
| fusion | logistic | 5.0 | 4 | 1.10 | 1.49 | 2.07 | 3.25 | 4.17 | 4.30 | 4.38 |
| fusion | random_forest | 1.0 | 5 | 0.40 | 0.44 | 0.50 | 0.60 | 1.00 | 1.00 | 1.00 |
| fusion | random_forest | 3.0 | 8 | 0.18 | 1.04 | 1.55 | 1.76 | 1.95 | 2.51 | 2.78 |
| fusion | random_forest | 5.0 | 4 | 0.40 | 0.71 | 1.16 | 2.61 | 4.03 | 4.44 | 4.72 |
| sensor_only | dummy_random | 5.0 | 3 | 0.10 | 0.76 | 1.75 | 3.40 | 4.20 | 4.68 | 5.00 |
| sensor_only | logistic | 1.0 | 1 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| sensor_only | logistic | 3.0 | 7 | 0.52 | 1.04 | 1.39 | 1.68 | 1.90 | 2.15 | 2.22 |
| sensor_only | logistic | 5.0 | 6 | 1.10 | 1.36 | 1.86 | 2.80 | 3.23 | 3.84 | 4.38 |
| sensor_only | random_forest | 1.0 | 8 | 0.10 | 0.24 | 0.38 | 0.60 | 0.85 | 1.00 | 1.00 |
| sensor_only | random_forest | 3.0 | 16 | 0.20 | 0.56 | 1.00 | 1.65 | 2.79 | 2.94 | 3.00 |
| sensor_only | random_forest | 5.0 | 5 | 1.10 | 1.46 | 2.00 | 2.30 | 2.50 | 3.04 | 3.40 |

## 4. Clinical + sensor fusion vs sensor-only (paired participant bootstrap)

Delta = fusion - sensor-only; interval from the same participant resamples. An interval containing 0 means the data do not distinguish the two.

| Model | Horizon | dAP [CI] | dBrier [CI] | dEvent sens. [CI] | dFalse alerts/h [CI] | dMedian lead s [CI] | verdict (AP) |
|---|---:|---|---|---|---|---|---|
| dummy_prior | 1.0 | 0.000 [0.000, 0.000] | 0.0000 [0.0000, 0.0000] | 0.00 [0.00, 0.00] | 0.0 [0.0, 0.0] | n/a | no distinguishable difference |
| dummy_prior | 3.0 | 0.000 [0.000, 0.000] | 0.0000 [0.0000, 0.0000] | 0.00 [0.00, 0.00] | 0.0 [0.0, 0.0] | n/a | no distinguishable difference |
| dummy_prior | 5.0 | 0.000 [0.000, 0.000] | 0.0000 [0.0000, 0.0000] | 0.00 [0.00, 0.00] | 0.0 [0.0, 0.0] | n/a | no distinguishable difference |
| dummy_random | 1.0 | 0.000 [0.000, 0.000] | 0.0000 [0.0000, 0.0000] | 0.00 [0.00, 0.00] | 0.0 [0.0, 0.0] | n/a | no distinguishable difference |
| dummy_random | 3.0 | 0.000 [0.000, 0.000] | 0.0000 [0.0000, 0.0000] | 0.00 [0.00, 0.00] | 0.0 [0.0, 0.0] | n/a | no distinguishable difference |
| dummy_random | 5.0 | 0.000 [0.000, 0.000] | 0.0000 [0.0000, 0.0000] | 0.00 [0.00, 0.00] | 0.0 [0.0, 0.0] | 0.00 [0.00, 0.00] | no distinguishable difference |
| logistic | 1.0 | 0.006 [-0.006, 0.018] | 0.0054 [-0.0220, 0.0301] | -0.01 [-0.04, 0.00] | -1.1 [-3.3, 0.0] | n/a | no distinguishable difference |
| logistic | 3.0 | 0.004 [-0.018, 0.026] | 0.0207 [-0.0069, 0.0548] | -0.07 [-0.21, 0.05] | -21.1 [-64.1, 7.3] | -0.77 [-1.17, 0.78] | no distinguishable difference |
| logistic | 5.0 | -0.001 [-0.029, 0.031] | 0.0091 [-0.0143, 0.0349] | -0.03 [-0.07, 0.00] | -8.1 [-23.2, 0.0] | 0.45 [0.45, 0.45] | no distinguishable difference |
| random_forest | 1.0 | -0.008 [-0.026, 0.017] | 0.0025 [-0.0012, 0.0064] | -0.04 [-0.13, 0.04] | 14.9 [-14.0, 47.8] | 0.00 [-0.42, 0.40] | no distinguishable difference |
| random_forest | 3.0 | 0.012 [-0.003, 0.029] | -0.0022 [-0.0123, 0.0069] | -0.11 [-0.25, 0.02] | -15.5 [-60.7, 22.8] | 0.11 [-2.62, 1.40] | no distinguishable difference |
| random_forest | 5.0 | 0.021 [-0.021, 0.048] | 0.0010 [-0.0111, 0.0125] | -0.01 [-0.20, 0.13] | 8.1 [-44.1, 56.6] | 0.31 [0.31, 0.31] | no distinguishable difference |

## 5. Preferred model per horizon (selection metric, not a claim of success)

- h1: sensor_only / random_forest (average_precision = 0.092)
- h3: fusion / logistic (average_precision = 0.161)
- h5: sensor_only / logistic (average_precision = 0.237)

## 6. Variability across folds and participants

- `fusion|logistic|h1` thresholds per fold [0.993, 1.0, 1.0, 1.0, 1.0]; folds: f0: AP 0.05, sens 0.00 (18 ev), f1: AP 0.07, sens 0.00 (14 ev), f2: AP 0.07, sens 0.00 (10 ev), f3: AP 0.04, sens 0.00 (17 ev), f4: AP 0.12, sens 0.00 (16 ev); participants with evaluable events: 15/22.
- `fusion|logistic|h3` thresholds per fold [0.996, 0.998, 1.0, 1.0, 1.0]; folds: f0: AP 0.23, sens 0.00 (18 ev), f1: AP 0.10, sens 0.00 (14 ev), f2: AP 0.09, sens 0.20 (10 ev), f3: AP 0.20, sens 0.00 (17 ev), f4: AP 0.22, sens 0.00 (16 ev); participants with evaluable events: 15/22.
- `fusion|logistic|h5` thresholds per fold [0.998, 1.0, 1.0, 1.0, 1.0]; folds: f0: AP 0.34, sens 0.22 (18 ev), f1: AP 0.22, sens 0.00 (14 ev), f2: AP 0.11, sens 0.00 (10 ev), f3: AP 0.29, sens 0.00 (17 ev), f4: AP 0.27, sens 0.00 (16 ev); participants with evaluable events: 15/22.
- `fusion|random_forest|h1` thresholds per fold [0.42, 0.482, 0.486, 0.495, 0.52]; folds: f0: AP 0.14, sens 0.06 (18 ev), f1: AP 0.13, sens 0.00 (14 ev), f2: AP 0.12, sens 0.10 (10 ev), f3: AP 0.08, sens 0.00 (17 ev), f4: AP 0.18, sens 0.19 (16 ev); participants with evaluable events: 15/22.
- `fusion|random_forest|h3` thresholds per fold [0.517, 0.541, 0.574, 0.594, 0.608]; folds: f0: AP 0.20, sens 0.06 (18 ev), f1: AP 0.12, sens 0.00 (14 ev), f2: AP 0.13, sens 0.50 (10 ev), f3: AP 0.13, sens 0.00 (17 ev), f4: AP 0.19, sens 0.12 (16 ev); participants with evaluable events: 15/22.
- `fusion|random_forest|h5` thresholds per fold [0.586, 0.612, 0.64, 0.735, 0.847]; folds: f0: AP 0.33, sens 0.22 (18 ev), f1: AP 0.19, sens 0.00 (14 ev), f2: AP 0.14, sens 0.00 (10 ev), f3: AP 0.13, sens 0.00 (17 ev), f4: AP 0.28, sens 0.00 (16 ev); participants with evaluable events: 15/22.
- `sensor_only|logistic|h1` thresholds per fold [0.993, 1.0, 1.0, 1.0, 1.0]; folds: f0: AP 0.06, sens 0.00 (18 ev), f1: AP 0.10, sens 0.00 (14 ev), f2: AP 0.05, sens 0.00 (10 ev), f3: AP 0.04, sens 0.00 (17 ev), f4: AP 0.11, sens 0.06 (16 ev); participants with evaluable events: 15/22.
- `sensor_only|logistic|h3` thresholds per fold [0.986, 0.992, 0.996, 1.0, 1.0]; folds: f0: AP 0.21, sens 0.33 (18 ev), f1: AP 0.11, sens 0.00 (14 ev), f2: AP 0.08, sens 0.10 (10 ev), f3: AP 0.19, sens 0.00 (17 ev), f4: AP 0.22, sens 0.00 (16 ev); participants with evaluable events: 15/22.
- `sensor_only|logistic|h5` thresholds per fold [0.999, 0.999, 0.999, 1.0, 1.0]; folds: f0: AP 0.31, sens 0.33 (18 ev), f1: AP 0.23, sens 0.00 (14 ev), f2: AP 0.10, sens 0.00 (10 ev), f3: AP 0.25, sens 0.00 (17 ev), f4: AP 0.25, sens 0.00 (16 ev); participants with evaluable events: 15/22.
- `sensor_only|random_forest|h1` thresholds per fold [0.437, 0.466, 0.494, 0.5, 0.543]; folds: f0: AP 0.06, sens 0.00 (18 ev), f1: AP 0.20, sens 0.21 (14 ev), f2: AP 0.14, sens 0.20 (10 ev), f3: AP 0.07, sens 0.00 (17 ev), f4: AP 0.18, sens 0.19 (16 ev); participants with evaluable events: 15/22.
- `sensor_only|random_forest|h3` thresholds per fold [0.522, 0.536, 0.574, 0.586, 0.615]; folds: f0: AP 0.18, sens 0.11 (18 ev), f1: AP 0.14, sens 0.36 (14 ev), f2: AP 0.11, sens 0.40 (10 ev), f3: AP 0.09, sens 0.00 (17 ev), f4: AP 0.19, sens 0.31 (16 ev); participants with evaluable events: 15/22.
- `sensor_only|random_forest|h5` thresholds per fold [0.6, 0.612, 0.636, 0.752, 0.863]; folds: f0: AP 0.27, sens 0.00 (18 ev), f1: AP 0.21, sens 0.36 (14 ev), f2: AP 0.14, sens 0.00 (10 ev), f3: AP 0.12, sens 0.00 (17 ev), f4: AP 0.32, sens 0.00 (16 ev); participants with evaluable events: 15/22.

## 7. Limitations

- Only a small number of participants contributes independent information; windows from one person are strongly correlated, so intervals are wide and fold estimates unstable.
- Clinical fusion has few independent clinical profiles to learn from; a lack of improvement is expected and a seeming improvement should be treated cautiously.
- Threshold and alert policy were fixed in configuration; event sensitivity depends strongly on the false-alert budget and on the monitored time (a few hours in total).
- Annotations come from 10 fps video; onset times are accurate only to that resolution. Recordings are structured lab tasks, not free-living monitoring.
- Windows that overlap or follow an ongoing episode are excluded by design, so events that follow another event within the input window cannot be evaluated; very short recordings contribute few windows.
- Sensor-quality filtering removes windows with missing sensors; results apply to the retained windows only.
- Scores are model outputs for research, not diagnoses; no claim of clinical effectiveness follows from retrospective data.

## 8. Citation

Credit the dataset authors, link the license and indicate modifications. Cite Borzì, L. et al. (2026) Sci Data 13, 305, https://doi.org/10.1038/s41597-026-06645-1 and the Zenodo record https://zenodo.org/records/17838806.
