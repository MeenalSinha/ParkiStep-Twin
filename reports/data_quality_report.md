# Data Quality Report - FoG-STAR: Freezing of Gait Severity, Tasks, Activities, and Ratings

**Status: `PASS_WITH_WARNINGS`**  
All critical checks passed (required columns, timestamp integrity, join validity, annotation availability, forecasting prerequisites); 10 warning(s) remain: ANNOTATION_UNDOCUMENTED_VALUE, CLINICAL_MISSING_VALUES, CLINICAL_OUT_OF_RANGE, JOIN_MISSING_CLINICAL_AFTER, RECORDING_BOUNDARIES_INVISIBLE_IN_TIMESTAMPS, SENSOR_MISSING_VALUES.

Generated (UTC): 2026-10-20T06:52:01+00:00  
Real-data audit: `run`

## 1. Dataset overview

- Version: 3.0 (as stated in the bundled README; the Zenodo record's own version tag could not be verified offline)
- Source: https://zenodo.org/records/17838806
- License: CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/)
- Attribution: Credit the dataset authors, link the license and indicate modifications. Cite Borzì, L. et al. (2026) Sci Data 13, 305, https://doi.org/10.1038/s41597-026-06645-1 and the Zenodo record https://zenodo.org/records/17838806.

| File | Size (bytes) | Rows | Columns | SHA-256 |
|---|---:|---:|---:|---|
| sensor: `sensor_data.csv` | 119,629,580 | 329,027 | 31 | `888875133fef386a...` |
| clinical: `clinical_data.csv` | 698 | 22 | 10 | `91aa90f70e25d33d...` |

| Quantity | Value |
|---|---:|
| n_participants_sensor | 22 |
| n_participants_clinical | 22 |
| n_sensor_rows | 329,027 |
| n_clinical_records | 22 |
| n_recordings | 106 |
| n_annotated_episodes | 101 |
| n_distinct_participant_id | 22 |
| n_distinct_session_id | 3 |
| n_distinct_task_id | 7 |

Documentation discrepancies noted in the profile (verified against the files):
- README says timestamp is in milliseconds; observed step is 1/60 (0.01667), i.e. the column is in seconds. The profile therefore sets timestamp.unit: seconds.
- README names clinical columns fes_i and pdq_8; the CSV header uses 'fes-i' and 'pdq8'. Both spellings are listed as aliases.
- README documents activity labels 1-7; the sensor file also contains activity value 0.

## 2. Clinical-data quality

Records: 22; distinct participants: 22; rows sharing a duplicated key: 0.

| Field | dtype | Missing | Missing % | Mean | Std | Min | Median | Max |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| participant_id | Int64 | 0 | 0.0 | n/a | n/a | n/a | n/a | n/a |
| age | float64 | 0 | 0.0 | 70.95 | 9.276 | 54 | 71 | 86 |
| gender | string | 0 | 0.0 | n/a | n/a | n/a | n/a | n/a |
| disease_duration | float64 | 1 | 4.5 | 10.1 | 6.789 | 2 | 9 | 25 |
| hoehn_yahr | float64 | 1 | 4.5 | 2.738 | 0.8459 | 1.5 | 2.5 | 4 |
| updrs_iii | float64 | 0 | 0.0 | 38.95 | 14.64 | 18 | 35 | 69 |
| fog_q | float64 | 0 | 0.0 | 18.27 | 5.382 | 7 | 19 | 27 |
| moca | float64 | 1 | 4.5 | 21.33 | 3.929 | 15 | 22 | 29 |
| fes_i | float64 | 1 | 4.5 | 36.95 | 26.95 | 11 | 27 | 100 |
| pdq_8 | float64 | 0 | 0.0 | 14.36 | 6.863 | 3 | 14 | 29 |

Categorical `gender`: M=12, F=10

## 3. Sensor-data quality

Rows: 329,027; participants: 22; recordings (unique group_by combinations): 106.

Missing sensor cells: 356,792; rows with any missing channel: 50,193 (15.25%).

| Sensor position | Rows with all channels missing | Rows partially missing |
|---|---:|---:|
| ankleL | 10,236 | 0 |
| ankleR | 9,490 | 2,281 |
| back | 24,120 | 4,238 |
| wrist | 10,211 | 5,911 |

Channels: 24 (ankleL_acc_x, ankleL_acc_y, ankleL_acc_z, ankleL_gyro_x, ankleL_gyro_y, ankleL_gyro_z, ...). Constant channels: none; channels with out-of-range values: 0.

### Timestamps and sampling
- Duplicate timestamps (within recording): 0
- Out-of-order steps: 0; non-contiguous recordings: 0
- Interval (s): median 0.016667, min 0.016667, max 0.016667, std 4.28e-12
- Estimated sampling frequency: 60.0000 Hz overall (per recording 60.0000-60.0000 Hz); configured expectation: 60 Hz
- Fraction of intervals within tolerance of reference: 1.0000
- Gaps above 0.0333s: 0
- Total recorded duration: 5482.0s across 106 recordings

## 4. Annotation quality

**activity** (activity): 0: 5,197 (1.58%), 1 (Walk): 124,913 (37.96%), 2 (Sit): 19,590 (5.95%), 3 (Stand): 47,806 (14.53%), 4 (Sit-to-stand): 9,924 (3.02%), 5 (Stand-to-sit): 7,977 (2.42%), 6 (Turn-right): 46,792 (14.22%), 7 (Turn-left): 66,828 (20.31%); missing 0; undocumented values [0]

**fog** (event_label): 0 (No FoG): 264,700 (80.45%), 1 (FoG): 64,327 (19.55%); missing 0; undocumented values none

**fog_severity** (severity): 0 (No FoG): 264,700 (80.45%), 1 (Shuffling): 5,996 (1.82%), 2 (Trembling): 13,976 (4.25%), 3 (Akinesia): 44,355 (13.48%); missing 0; undocumented values none

Episodes: **101** (contiguous run of 'fog' == 1 within one recording (group_by=['participant_id', 'session_id', 'task_id'])); participants with episodes: 16.
Episode duration (s): mean 10.62, median 5.30, min 0.60, max 108.68.

Episodes per participant: 1: 5, 2: 13, 3: 7, 4: 2, 5: 1, 9: 12, 11: 9, 12: 4, 13: 3, 15: 10, 16: 1, 17: 5, 19: 9, 20: 6, 21: 10, 22: 4

Severity/event consistency: {'severity_without_event': 0, 'event_without_severity': 0, 'episodes_with_multiple_severity_values': 6}
Annotation-frame alignment (informational): 194 of 299 label runs are multiples of 6 samples (0.649).

## 5. Join integrity

- Key: `participant_id`; type: left; expected cardinality: many_to_one; observed: many_to_one
- Distinct participants: sensor 22, clinical 22, matched 22
- Sensor participants without clinical record: none
- Clinical participants without sensor data: none
- Sensor rows before/after: 329,027 / 329,027
- Join validation passed: **True**

## 6. Data readiness summary

**PASS_WITH_WARNINGS** - All critical checks passed (required columns, timestamp integrity, join validity, annotation availability, forecasting prerequisites); 10 warning(s) remain: ANNOTATION_UNDOCUMENTED_VALUE, CLINICAL_MISSING_VALUES, CLINICAL_OUT_OF_RANGE, JOIN_MISSING_CLINICAL_AFTER, RECORDING_BOUNDARIES_INVISIBLE_IN_TIMESTAMPS, SENSOR_MISSING_VALUES.

- PASS: annotated_episodes_available (required >= 1, observed 101)
- PASS: participants_with_episodes (required >= 2, observed 16)

Phase 1 only validates source data and annotations; it does not establish a forecasting horizon or any predictive performance.

## Issues

### Warnings (10)
- **CLINICAL_MISSING_VALUES**: 'disease_duration' is missing for 1 participant(s) (ids: [22]); values are retained as missing, not imputed. _(at clinical.fields.disease_duration)_
- **CLINICAL_MISSING_VALUES**: 'hoehn_yahr' is missing for 1 participant(s) (ids: [12]); values are retained as missing, not imputed. _(at clinical.fields.hoehn_yahr)_
- **CLINICAL_OUT_OF_RANGE**: 'fog_q' has 3 value(s) outside the documented range [0, 24] (participants [11, 19, 22]; values [25.0, 27.0, 27.0]). Flagged for review, not altered. _(at clinical.fields.fog_q.valid_range)_
- **CLINICAL_MISSING_VALUES**: 'moca' is missing for 1 participant(s) (ids: [11]); values are retained as missing, not imputed. _(at clinical.fields.moca)_
- **CLINICAL_MISSING_VALUES**: 'fes_i' is missing for 1 participant(s) (ids: [21]); values are retained as missing, not imputed. _(at clinical.fields.fes_i)_
- **CLINICAL_OUT_OF_RANGE**: 'fes_i' has 9 value(s) outside the documented range [16, 64] (participants [4, 5, 6, 11, 13, 15, 18, 19, 22]; values [11.0, 15.0, 12.0, 84.0, 65.0, 80.0, 13.0, 100.0, 70.0]). Flagged for review, not altered. _(at clinical.fields.fes_i.valid_range)_
- **SENSOR_MISSING_VALUES**: 50,193 of 329,027 sensor rows (15.3%) have at least one missing channel value; retained as missing (no imputation or deletion). _(at sensor channels)_ Action: Phase 2 must define an explicit policy for windows containing missing samples.
- **RECORDING_BOUNDARIES_INVISIBLE_IN_TIMESTAMPS**: 105 of 105 consecutive recording boundaries are exactly one sampling step apart: the timestamp is one running clock across recordings, so gap checks cannot detect recording boundaries. _(at timestamp_s)_ Action: Phase 2 windowing must group by the recording key (group_by), never by timestamp continuity alone.
- **ANNOTATION_UNDOCUMENTED_VALUE**: 'activity' contains value(s) [0] not listed in the documentation/profile (5,197 rows). Kept unchanged. _(at sensor.annotations.activity.allowed_values)_ Action: Confirm their meaning with the dataset authors before using this label in Phase 2.
- **JOIN_MISSING_CLINICAL_AFTER**: Some clinical attributes are missing for joined sensor rows (propagated from missing clinical values): disease_duration=6,249, hoehn_yahr=17,287, moca=13,855, fes_i=10,992 _(at joined view)_

