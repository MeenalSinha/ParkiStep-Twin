# ParkiStep Twin

Research proof of concept: forecasting the onset of freezing-of-gait (FoG) episodes in Parkinson's disease by fusing
participant-level clinical data with wearable IMU signals (FoG-STAR dataset). **Not a diagnostic or clinical device.**

Phase 0 (requirements, architecture) is preserved unchanged in `docs/phase0/`.

## Phase 1 status: Data Foundation and Quality Assurance

Implemented and run on the real FoG-STAR files (supplied as `Dataset.zip`): acquisition, ingestion, schema validation,
quality audit, clinical-sensor join audit, processed outputs, figures, tests, documentation.
Latest real-data audit: **`PASS_WITH_WARNINGS`** - see `reports/data_quality_report.md`.
Phase 1 was re-verified at the start of Phase 2 (`docs/phase2/PHASE1_VERIFICATION.md`).

## Setup

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt                        # pyarrow is optional (CSV fallback is configurable)
```

## Get the data (not committed to Git)

Place `sensor_data.csv` and `clinical_data.csv` in `data/raw/fogstar/`, or:

```bash
python scripts/acquire_data.py --from-archive path/to/Dataset.zip   # extract from a local archive
python scripts/acquire_data.py --download                           # from Zenodo record 17838806 (needs network)
```
Details, licence and attribution: `data/README.md`, `docs/DATASET_PROVENANCE.md`.

## Run Phase 1

```bash
python scripts/prepare_data.py          # audit + reports + figures + processed outputs (one command)
python scripts/audit_data.py            # audit, reports and figures only
python -m pytest                        # 43 tests; no real data or network needed
```
Equivalent: `python -m parkistep [options] {acquire|audit|prepare}` (set `PYTHONPATH=src` or `pip install -e .`).
Useful options: `--config`, `--project-config`, `--dataset`, `--data-root`, `--processed-dir`, `--reports-dir`,
`--figures-dir`, `--figures a,b|none`, `--no-reports`, `--log-level`, `--set key=value`, `--fail-on-warnings`.
Exit codes: 0 pass, 1 audit FAIL/NOT_RUN (or warnings with `--fail-on-warnings`), 2 config/pipeline error.

## Configuration

All paths, column mappings, dtypes, units, sampling expectations, label definitions and validation thresholds live in
`configs/project.yaml` and `configs/datasets.yaml`; reference in `docs/configuration.md`. No source edits are needed to
change them.

## Layout

```
configs/    project.yaml, datasets.yaml        src/parkistep/   config, acquisition, ingestion, validation,
data/       raw|interim|processed (git-ignored)                 quality, integration, pipeline, reporting,
docs/       phase0/, phase1_data_foundation.md,                 visualization, cli
            configuration.md, DATASET_PROVENANCE.md
reports/    data_quality_report.{md,json}, join_audit_report.json, figures/, test_report.md
scripts/    acquire_data.py, audit_data.py, prepare_data.py     tests/  synthetic-fixture tests
```

## Attribution

FoG-STAR (CC BY 4.0): Borzì, L. et al. (2026), Sci Data 13, 305, https://doi.org/10.1038/s41597-026-06645-1;
Zenodo record https://zenodo.org/records/17838806. Processed files in `data/processed/` are modified derivatives
(see `dataset_manifest.json`). Original code licence: MIT proposed in Phase 0; no LICENSE file has been added yet.

## Phase 2: forecasting target and baseline models

Pre-onset forecasting of freezing episodes (horizons, window and thresholds all in `configs/phase2.yaml`), sensor-only vs
clinical+sensor models, participant-grouped evaluation, event-level warning metrics, inference interface. Method:
`docs/phase2/PHASE2_METHODOLOGY.md`; Phase 1 check: `docs/phase2/PHASE1_VERIFICATION.md`; results (generated from the run):
`reports/phase2/phase2_results_report.md`.

```bash
pip install -r requirements.txt                    # scikit-learn, scipy, joblib added for Phase 2
python scripts/prepare_data.py                     # Phase 1 audit + processed tables (needs data/raw/fogstar/*)
python scripts/prepare_phase2_windows.py           # events, windows, labels, features -> data/interim/phase2/
python scripts/train_phase2.py                     # grouped CV, thresholds, final models -> artifacts/phase2/<id>/
python scripts/evaluate_phase2.py                  # metrics + reports/phase2/
python -m pytest                                   # Phase 1 + Phase 2 tests (synthetic fixtures, offline)
```
Overrides without editing code: `--set windowing.horizons_s=[2.0,4.0] --set features.frequency.enabled=false`.
Inspect predictions: `artifacts/phase2/<id>/oof/*.parquet` (one row per held-out window with ids, probability, threshold).
Inference: `from parkistep.forecast.inference import ForecastPredictor` (see methodology doc). Outputs are research scores,
not diagnoses. Limitations (22 participants, ~100 events, lab tasks) are listed in the methodology and results report.

## Phase 3: digital-twin engine

> **Outputs are research model scores, not diagnoses.** The Phase 2 model was trained on 22 participants, is not clinically
> validated, has low and variable event sensitivity, and produces uncalibrated scores. A "warning" is the output of a
> configurable policy applied to those scores; it does not establish that a freezing episode is occurring or will occur.

Phase 3 turns the Phase 2 forecaster into a continuously updated, per-patient **digital twin**: it takes sensor observations one
at a time (a real FoG-STAR recording can be replayed as if it came from a wearable), keeps a bounded rolling window, calls the
trained Phase 2 model only when the window satisfies the model's input requirements, manages warnings with a configurable
policy, and keeps an auditable, optionally persisted history for a later dashboard. The dashboard itself is a later phase.

**Requirements.** Phase 1 outputs (`python scripts/prepare_data.py`) and the trained Phase 2 artifacts
(`artifacts/phase2/<experiment_id>/models/*.joblib` + manifests). Nothing is trained in Phase 3; if an artifact is missing or
fails its checksum the commands stop with an explanation instead of substituting another model.

```bash
python scripts/verify_twin_model.py            # artifact gate: file + SHA-256 + schema + a real prediction on a real window
python scripts/replay_twin.py                  # replay the selected real recording through the engine; writes reports/phase3/
python scripts/create_twin.py --patient 3      # create a twin (clinical record read from the Phase 1 table)
python scripts/inspect_twin.py --list          # persisted twins;  --patient 2 [--predictions 10] [--events STATUS_CHANGE]
python -m pytest                               # all tests (Phase 1 + 2 + 3)
```

Configure everything in `configs/phase3.yaml` or per run with `--set`, e.g. `--set model.experiment=fusion --set model.model=logistic
--set model.horizon_s=5.0`. The model's input window, feature schema, horizon and operating threshold come from the saved model
manifest, not from this file; the horizon in the configuration must equal the artifact's. State is stored in
`artifacts/phase3/twin_state.sqlite` (path configurable); an existing twin is never overwritten unless `persistence.overwrite` is true.

Important when reading replay results: the saved Phase 2 models were fitted on **all** participants, so replaying a dataset
recording is an in-sample software demonstration, not evidence of predictive performance. Participant-grouped Phase 2 results
(`reports/phase2/`) remain the reference for expected behaviour.

Documentation: `docs/phase3/PHASE2_VERIFICATION.md` (what was re-verified and the exit decision),
`docs/phase3/PHASE3_IMPLEMENTATION.md` (architecture, state schema, lifecycle, warning policy, persistence, configuration
reference, limitations), `docs/phase3/PHASE3_COMPLETION_REPORT.md` (what was built, tests, real results).


## Phase 4 - clinician dashboard (research prototype; outputs are model scores, not diagnoses)
**Purpose.** Inspect a virtual patient, replay a real FoG-STAR recording through the Phase 3 digital twin, watch state/forecasts/warnings update, and review the measured Phase 2 performance and limitations. The dashboard only displays what the engine and the saved evaluation produce.

**Install.** `pip install -r requirements.txt` (adds `streamlit>=1.40`, `plotly`; `pyarrow` is needed to read the parquet tables).
**Prepare (once).** `python scripts/prepare_data.py`; Phase 2 artifacts must exist in `artifacts/phase2/<experiment>/models` (verify: `python scripts/verify_twin_model.py`).
**Launch.** `streamlit run app/Home.py` (from the project root).
**Configure.** Edit `configs/phase4.yaml`, or `PARKISTEP_PHASE4_SET="replay.default_speed=10"`. Model/horizon: Phase 3 `model.*` or the model selector on Replay Analysis.
**Replay.** Replay Analysis -> choose patient, recording, model, speed -> Start. Pause/Resume/Stop at any time; other pages update live. After the end the retrospective analysis appears.
**Sections.** Overview (patient, twin, stream, forecast, warning, data quality) - Patient Profile (clinical record; missing stays missing) - Movement Monitor (observed channels, gaps) - Early Warning (latest forecast and history) - Warning Center (warnings, their predictions, acknowledgement via the engine) - Replay Analysis - Model Performance (saved participant-grouped results).
**Limitations.** Weak, variable out-of-sample sensitivity and many false alerts; uncalibrated scores; in-sample replays; small laboratory cohort. Not clinically validated. Details: `docs/phase4/`.
