# Phase 3 verification (performed at the start of Phase 4)

**Exit status: `PASS_WITH_NON_BLOCKING_WARNINGS`** - the data foundation, model artifacts and digital-twin engine were independently
re-run and work; the pytest suites could **not** be re-executed in this environment (see W1).

## Environment of this verification
Sandbox without network access. Installed: Python 3.12, pandas, numpy, scikit-learn, joblib, PyYAML. **Not installed and not installable:**
`pytest`, `pyarrow`, `streamlit`, `plotly`. Work was done on a scratch copy of the repository; `data/processed` was regenerated as CSV by
the existing Phase 1 pipeline (its documented `fallback_format: csv`) because the shipped parquet files need `pyarrow`.

## What was executed now (not copied from reports)
| Check | Result |
|---|---|
| Phase 1 pipeline on the real FoG-STAR CSVs (`scripts/prepare_data.py`) | Audit `PASS_WITH_WARNINGS`; all critical checks passed; **10** warnings (ANNOTATION_UNDOCUMENTED_VALUE, CLINICAL_MISSING_VALUES, CLINICAL_OUT_OF_RANGE, JOIN_MISSING_CLINICAL_AFTER, RECORDING_BOUNDARIES_INVISIBLE_IN_TIMESTAMPS, SENSOR_MISSING_VALUES, ...) - all data-description warnings, none blocking. Tables: 329,027 sensor rows, 22 participants, 106 recordings (matches the Phase 3 anchors). |
| Phase 2 artifacts | 24/24 manifests: model file present and SHA-256 equals the manifest value. |
| `verify_twin_model.py` (real recording `p2-s1-t4`, real trailing window) | exit 0; score 0.7283 (finite), threshold 0.7032 from `model_manifest`, horizon 3.0 s, window end = prediction time, 0 samples after the window end, data quality `ok`, 516/516 features finite. Identical to the Phase 3 report. |
| Real replay (`scripts/replay_twin.py --no-persist`, default config) | 11,176 observations processed / 0 rejected; 922 forecasts; 14 warnings (all resolved); 0 future-data violations; **determinism digest `13ee0b1870bc054f...` identical to the Phase 3 report**; retrospective: 7 events, 1 detected, sensitivity 0.143, median lead 2.4 s (in-sample). |
| Phase 1-3 source files | Byte-identical to the uploaded archive (diff of the tree shows only added files). |

## Checklist outcome
Checklists 1, 2 (data load, audit, joins, boundaries), 3 (artifacts, checksums, schema, horizon compatibility, out-of-sample reports present),
4 (engine lifecycle, bounded buffer, forecast integration, warning manager, replay, reproducibility) and 5 (real model through the gateway) were
verified by the runs above and by reading the code. Phase 2 metrics shown by the dashboard are read from `reports/phase2/phase2_metrics.json`.

## Warnings (non-blocking)
* **W1 - pytest suites not re-run.** The reported 282 passing tests (109 Phase 1/2 + 173 Phase 3) could not be re-executed because pytest is
  unavailable here. Their result is therefore *historical*, not verified now. Run `PARKISTEP_REQUIRE_REAL=1 python -m pytest` in your environment.
* **W2 - parquet needs pyarrow.** `data/processed/*.parquet` cannot be read without it (`pip install pyarrow`); `read_table` prefers parquet over CSV.
* W3 - The Phase 3 limitations carry over unchanged: weak out-of-sample performance (default model sensitivity 0.21 [0.04-0.35], about 74 false alerts/h at 3 s),
  uncalibrated scores, in-sample replays, Zenodo version tag unverifiable offline.

No repair of Phase 1, 2 or 3 was necessary and none was made.
