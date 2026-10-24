# Phase 1 - Data Foundation and Quality Assurance

## Starting state
The repository contained only the Phase 0 pack (requirements MD/DOCX/PDF, `phase0_config.yaml`, architecture diagram) and no code,
tests or dependencies. Phase 0 files were copied unchanged to `docs/phase0/`. No Phase 0 decision was altered. Phase 1 does confirm
two Phase 0 assumptions with data: 60 Hz sampling and the 22-participant / 101-episode scope (see the report).
Phase 0's `join_key: subjectID` is the *source* column; the pipeline uses canonical `participant_id` mapped via config.

## Architecture
`ingestion` (SourceAdapter: discover, load raw tables + SHA-256) -> `validation` (alias mapping, dtype conversion, clinical rules)
-> `quality` (missing, duplicates, ordering, sampling/gaps, constants, annotations, episodes, readiness) -> `integration`
(participant join + audit) -> `pipeline` (status, processed outputs, manifest) -> `reporting` / `visualization`; `acquisition`
handles local-archive extraction and verified download; `cli` + `scripts/` are the entry points.

## The two streams
* Stream A, clinical: 22 rows, one per participant (age, gender, disease duration, H&Y, MDS-UPDRS III, FoG-Q, MoCA, FES-I, PDQ-8).
* Stream B, sensor: 24 IMU channels (4 positions x acc/gyro x xyz), timestamps, ids, activity, FoG, severity.
Joined only on the configured key; expected many-to-one; never positional.

## Validation behaviour
Issues are `error` (critical: stop that processing step, status FAIL), `warning`, or `info`; each names the file/config entry and next action.
Status: `NOT_RUN` (files absent - no statistics emitted), `FAIL`, `PASS_WITH_WARNINGS`, `PASS`. Readiness = no critical errors,
including required columns, timestamp integrity, join validity, annotations present and the `readiness` thresholds. Timestamp checks
are per recording (`group_by`), so timestamp resets between recordings are not errors. Nothing is imputed, clipped, dropped or re-sorted.

## Processing decisions
Timestamps converted to seconds via the configured unit only. Episode = contiguous run of the configured event label inside one
recording (source annotation, not a forecasting label). `recording_id` is built from the first letter of each canonical group field
plus its value (e.g. `p1-s1-t1`); `source_row` keeps the raw row index. Output rows keep source order.

## Real-data results (FoG-STAR files supplied)
Computed by the pipeline, see `reports/data_quality_report.md`: 22 participants, 329,027 rows, 106 recordings, 101 episodes
(16 participants with episodes), 60.000 Hz in every recording, no duplicate/out-of-order timestamps or gaps, join 22/22 matched with
no row multiplication. Status `PASS_WITH_WARNINGS` because of: 15.3% of sensor rows with a missing channel (whole-sensor dropouts),
5 clinical values missing, FoG-Q (3 participants) and FES-I (9 participants) outside the README ranges, activity value 0 undocumented.
These are flagged for the dataset authors / Phase 2 policy, not corrected.

## Known limitations
* Zenodo record not reachable from the build environment: live download, record version, DOI and licence text unverified; the
  version "3.0" and CC BY 4.0 come from the bundled README / Phase 0 pack.
* Daphnet adapter not implemented (optional; the adapter interface is the extension point).
* Sensor `plausible_range`s are heuristics; no documented clinical range exists for age and disease duration.
* Annotation-frame alignment (10 fps video vs 60 Hz) is informational only.
* Phase 2 must decide handling of windows with missing sensor data and the undocumented activity 0.
